#!/usr/bin/env python3
"""Sends a backup of config/, profiles.json and letters/ to Telegram.

Run from cron (`python3 /app/backup.py`) or from the bot's «💾 Бэкап» button.
The archive holds HH tokens, cookies and the OpenAI key, so set BACKUP_PASSWORD
to encrypt it with openssl (aes-256-cbc, pbkdf2) before it lands in the chat.
"""
import io
import os
import sys
import json
import time
import tarfile
import datetime as dt
import subprocess
import urllib.request
from pathlib import Path

APP_DIR = Path(os.environ.get("APP_DIR", "/app"))
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/app/config"))
TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT_ID", "")
API = f"https://api.telegram.org/bot{TG_TOKEN}"

# Лог и рабочие файлы авторизации в бэкапе не нужны: лог большой, а флаги
# и капча — мусор одного запуска.
SKIP_NAMES = {"log.txt", ".auth_captcha.png", ".auth_output", ".auth_input",
              ".auth_request", ".auth_status", ".auth_cancel", ".apply.pid", ".cron_environment.json", ".apply.lock"}
MAX_TELEGRAM_BYTES = 45 * 1024 * 1024


def _keep(path):
    return path.name not in SKIP_NAMES and not path.name.endswith(".tmp")


def make_archive():
    """Собирает tar.gz в памяти. Возвращает (bytes, список включённых путей)."""
    buf = io.BytesIO()
    included = []
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for source in (CONFIG_DIR, APP_DIR / "letters"):
            if not source.is_dir():
                continue
            for path in sorted(source.rglob("*")):
                if path.is_file() and _keep(path):
                    tar.add(path, arcname=str(path.relative_to(APP_DIR)))
                    included.append(str(path.relative_to(APP_DIR)))
        profiles = APP_DIR / "profiles.json"
        if profiles.is_file():
            tar.add(profiles, arcname="profiles.json")
            included.append("profiles.json")
    return buf.getvalue(), included


def encrypt(data, password):
    """Шифрует openssl'ом. Возвращает (bytes, ok). ok=False — openssl недоступен."""
    try:
        res = subprocess.run(
            ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt",
             "-pass", "env:BACKUP_PASSWORD"],
            input=data, capture_output=True,
            env={**os.environ, "BACKUP_PASSWORD": password}, timeout=120,
        )
        if res.returncode == 0 and res.stdout:
            return res.stdout, True
        print(f"openssl failed: {res.stderr[:200]!r}", file=sys.stderr)
    except FileNotFoundError:
        print("openssl not found — sending unencrypted", file=sys.stderr)
    except Exception as e:
        print(f"encrypt failed: {e}", file=sys.stderr)
    raise RuntimeError("Encryption failed; backup was not sent")


def send_document(data, filename, caption):
    boundary = "----hhbackup" + os.urandom(8).hex()
    b = boundary.encode()
    parts = [
        b"--" + b + b"\r\n",
        b'Content-Disposition: form-data; name="chat_id"\r\n\r\n',
        str(TG_CHAT).encode() + b"\r\n",
        b"--" + b + b"\r\n",
        b'Content-Disposition: form-data; name="caption"\r\n\r\n',
        caption.encode("utf-8") + b"\r\n",
        b"--" + b + b"\r\n",
        f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'.encode(),
        b"Content-Type: application/octet-stream\r\n\r\n",
        data + b"\r\n",
        b"--" + b + b"--\r\n",
    ]
    req = urllib.request.Request(f"{API}/sendDocument", data=b"".join(parts))
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def send_via_vk(data, filename, caption):
    sys.path.insert(0, str(APP_DIR))
    import vk_transport
    vk = vk_transport.VKTransport()
    return vk.send_file(vk.peer_id, data, filename, caption)


def run():
    transport = os.environ.get("BOT_TRANSPORT", "telegram").strip().lower()
    if transport != "vk" and not (TG_TOKEN and TG_CHAT):
        print("TG credentials missing", file=sys.stderr)
        return 1

    data, included = make_archive()
    if not included:
        print("nothing to back up", file=sys.stderr)
        return 1

    password = os.environ.get("BACKUP_PASSWORD", "").strip()
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    if password:
        data, ok = encrypt(data, password)
        name = f"hh-backup-{stamp}.tar.gz.enc" if ok else f"hh-backup-{stamp}.tar.gz"
        note = ("🔒 зашифрован" if ok else "⚠️ БЕЗ шифрования: openssl недоступен")
    else:
        name = f"hh-backup-{stamp}.tar.gz"
        note = "⚠️ без шифрования: не задан BACKUP_PASSWORD"

    size_mb = len(data) / 1024 / 1024
    if len(data) > MAX_TELEGRAM_BYTES:
        print(f"archive too big for Telegram: {size_mb:.1f} MB", file=sys.stderr)
        return 1

    caption = (f"💾 Бэкап {stamp}\n"
               f"Файлов: {len(included)}, размер: {size_mb:.1f} МБ\n{note}")
    if transport == "vk":
        resp = send_via_vk(data, name, caption)
        resp = {"ok": bool(resp)}
    else:
        resp = send_document(data, name, caption)
    if not (resp or {}).get("ok"):
        print(f"send failed: {resp}", file=sys.stderr)
        return 1
    print(f"backup sent: {name} ({size_mb:.1f} MB, {len(included)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(run())
