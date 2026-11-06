"""Auth helper daemon.

Runs inside hh_applicant_tool using the upstream requests-based authorization.
Watches for /app/config/.auth_request from the bot, runs interactive
`hh-applicant-tool --profile X --manual-captcha --use-kitty auth`, and pipes its stdin/stdout
through files for the bot to consume.

Protocol (all files in /app/config/):
  .auth_request   - bot writes: {"profile": "main", "ts": <unix>}, helper reads & deletes
  .auth_output    - helper appends utility's stdout lines, bot polls & deletes
  .auth_input     - bot writes user's reply, helper reads & deletes
  .auth_status    - helper writes: {"state": "running|done|error|idle", "msg": "..."}
"""
import json
import os
import sys
import time
import signal
import threading
import subprocess
import base64
import re
from hh_compat import cli_args
from security import redact
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/app/config"))
REQ_FILE = CONFIG_DIR / ".auth_request"
OUT_FILE = CONFIG_DIR / ".auth_output"
IN_FILE = CONFIG_DIR / ".auth_input"
STATUS_FILE = CONFIG_DIR / ".auth_status"
CAPTCHA_FILE = CONFIG_DIR / ".auth_captcha.png"

POLL_INTERVAL = 0.5  # seconds

current_process = None
auth_generation = 0  # увеличивается на каждый запуск; вытесняет предыдущий
process_lock = threading.Lock()


def write_status(state, msg=""):
    """Write current state to status file."""
    try:
        with STATUS_FILE.open("w") as f:
            json.dump({"state": state, "msg": msg, "ts": time.time()}, f)
    except Exception as e:
        print(f"write_status error: {e}", flush=True)


def append_output(text):
    """Append a chunk of utility's stdout for the bot to read."""
    try:
        # Read existing output, append, write back
        existing = ""
        if OUT_FILE.exists():
            existing = OUT_FILE.read_text()
        with OUT_FILE.open("w") as f:
            f.write(existing + redact(text))
    except Exception as e:
        print(f"append_output error: {e}", flush=True)


def cleanup_files():
    """Remove all comm files (used at start to clear stale state)."""
    for f in (REQ_FILE, OUT_FILE, IN_FILE, CAPTCHA_FILE):
        try:
            if f.exists():
                f.unlink()
        except Exception:
            pass


def read_user_input(timeout=180):
    """Wait for the bot to write user's reply. Returns the text or None on timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if IN_FILE.exists():
            try:
                text = IN_FILE.read_text()
                IN_FILE.unlink()
                return text.rstrip("\n") + "\n"
            except Exception as e:
                print(f"read_user_input error: {e}", flush=True)
                return None
        time.sleep(POLL_INTERVAL)
    return None  # timeout


B64_PNG_START = b"iVBORw0KGgo"
PROMPT_MARK = "Введите текст с картинки".encode("utf-8")


def strip_captcha(buf):
    """Extract upstream Kitty APC PNG image and relay it to Telegram.

    Kitty graphics uses ESC_G <params> ; <base64> ESC\\, optionally chunked.
    Never expose image bytes in the text output or logs.
    """
    marker = b"\x1b_G"
    if marker not in buf:
        return "none", buf
    start = buf.index(marker)
    end = buf.find(b"\x1b\\", start)
    if end < 0:
        return "incomplete", buf
    packet = buf[start + len(marker):end]
    if b";" not in packet:
        return "done", buf[:start] + b"[captcha graphics]" + buf[end + 2:]
    params, payload = packet.split(b";", 1)
    # Single-packet PNG is supported; chunks with m=1 are buffered below.
    try:
        image = base64.b64decode(payload, validate=True)
        if image.startswith(b"\x89PNG\r\n\x1a\n") and len(image) <= 5_000_000:
            CAPTCHA_FILE.write_bytes(image)
    except (ValueError, OSError):
        pass
    return "done", buf[:start] + b"[captcha image]\n" + buf[end + 2:]


def stdout_reader_thread(proc, stop_event):
    """Read utility's stdout in chunks, append to OUT_FILE.
    The kitty captcha block (base64 image) is stripped from the text channel
    (replaced with a '[captcha image]' marker) so it doesn't flood Telegram;
    the actual PNG is saved to CAPTCHA_FILE by the patched utility itself.
    Flush text on newline or prompt-like ending."""
    fd = proc.stdout.fileno()
    buf = b""
    while not stop_event.is_set():
        try:
            chunk = os.read(fd, 65536)
        except Exception as e:
            print(f"stdout_reader error: {e}", flush=True)
            break
        if not chunk:  # EOF
            break
        buf += chunk

        status, buf = strip_captcha(buf)
        if status == "incomplete":
            continue  # ждём конец блока капчи, текст пока не флашим

        if b"\n" in buf or buf.endswith(b": ") or buf.endswith(b"? "):
            append_output(buf.decode("utf-8", errors="replace"))
            buf = b""
    # Flush any remaining buffer
    if buf:
        append_output(buf.decode("utf-8", errors="replace"))


def stdin_writer_thread(proc, stop_event):
    """Wait for input from bot, write to utility's stdin."""
    while not stop_event.is_set():
        if proc.poll() is not None:
            break
        text = read_user_input(timeout=180)
        if text is None:
            if proc.poll() is not None:
                break
            print("stdin_writer: timeout waiting for input", flush=True)
            write_status("error", "Timeout waiting for user input (3 min)")
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
            break
        try:
            proc.stdin.write(text)
            proc.stdin.flush()
        except Exception as e:
            print(f"stdin_writer error: {e}", flush=True)
            break


def run_auth(profile):
    """Run hh-applicant-tool auth for given profile, piping I/O through files."""
    global current_process, auth_generation

    # Claim a new generation. Any older run that finishes later will see its
    # generation is stale and stay silent (no status/current_process clobber).
    with process_lock:
        auth_generation += 1
        my_gen = auth_generation

    cleanup_files()
    write_status("running", f"Starting auth for profile {profile}")

    cmd = cli_args(profile, "--manual-captcha", "--use-kitty", "auth")
    print(f"Running: {' '.join(cmd)}", flush=True)

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # merge stderr into stdout
            text=True,
            bufsize=0,  # unbuffered
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    except FileNotFoundError:
        write_status("error", "hh-applicant-tool not found in PATH")
        return
    except Exception as e:
        write_status("error", f"Failed to start: {e}")
        return

    with process_lock:
        current_process = proc

    stop_event = threading.Event()
    t_out = threading.Thread(target=stdout_reader_thread, args=(proc, stop_event), daemon=True)
    t_in = threading.Thread(target=stdin_writer_thread, args=(proc, stop_event), daemon=True)
    t_out.start()
    t_in.start()

    # Wait for process to finish (with hard timeout)
    rc = None
    try:
        rc = proc.wait(timeout=600)  # 10 min hard limit
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        rc = -1

    stop_event.set()
    t_out.join(timeout=2)
    t_in.join(timeout=2)

    # Only the latest generation owns the shared state / final status.
    with process_lock:
        superseded = (my_gen != auth_generation)
        if not superseded:
            current_process = None

    if superseded:
        return  # newer auth took over — don't overwrite its status

    if rc == 0:
        write_status("done", f"Authentication successful for {profile}")
    elif rc == -1:
        write_status("error", "Auth process exceeded 10 min, killed")
    else:
        write_status("error", f"Auth failed (exit code {rc})")


def _terminate_current():
    """Terminate a lingering auth process so a new request can take over."""
    with process_lock:
        proc = current_process
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def handle_request(data):
    """Validate parsed request and dispatch. A new request takes priority:
    any lingering auth is terminated first (no 'Another auth' dead-end)."""
    profile = data.get("profile")
    if not isinstance(profile, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", profile):
        write_status("error", "Invalid profile identifier")
        return

    _terminate_current()
    run_auth(profile)


def cancel_current():
    """Cancel the running auth process (called on .auth_cancel)."""
    global auth_generation, current_process
    with process_lock:
        auth_generation += 1  # supersede the running auth so it stays silent
        proc = current_process
        current_process = None
    if proc is None:
        return
    print("Cancelling current auth process", flush=True)
    try:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    write_status("error", "Cancelled by user")


def main():
    print("auth_helper started", flush=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_files()
    write_status("idle", "Ready")

    cancel_file = CONFIG_DIR / ".auth_cancel"

    while True:
        try:
            if REQ_FILE.exists():
                # Read & remove the request in the main loop (single-threaded)
                # so the same file can't spawn two workers.
                data = None
                try:
                    data = json.loads(REQ_FILE.read_text())
                except Exception as e:
                    print(f"Invalid request file: {e}", flush=True)
                finally:
                    try:
                        REQ_FILE.unlink()
                    except Exception:
                        pass
                if data:
                    threading.Thread(
                        target=handle_request, args=(data,), daemon=True
                    ).start()
            if cancel_file.exists():
                cancel_file.unlink()
                cancel_current()
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"main loop error: {e}", flush=True)
            time.sleep(1)


if __name__ == "__main__":
    main()
