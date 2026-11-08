#!/usr/bin/env python3
"""Builds apply-vacancies command from the active profile and execs it.

Doing this in Python (not bash) avoids word-splitting problems with
search queries that contain spaces, quotes, OR/NOT operators and parens.
"""
import os
import re
import sys
import time
import subprocess
import json
from hh_compat import cli_args
from security import redact
from storage import file_lock
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import profiles_lib

LETTERS_DIR = os.path.join(os.environ.get("APP_DIR", os.path.dirname(os.path.abspath(__file__))), "letters")
PAUSE_FLAG = os.path.join(os.environ.get("CONFIG_DIR", "/app/config"), ".paused")

# Один вызов apply-vacancies обходит ограниченное число страниц поиска и на
# этом заканчивается, даже если суточный лимит ещё не выбран, — поэтому ручной
# повторный «Старт» и продолжал рассылку. Повторяем проходы сами, пока они
# приносят новые отклики.
MAX_PASSES = int(os.environ.get("APPLY_MAX_PASSES", "10"))
PASS_DELAY = int(os.environ.get("APPLY_PASS_DELAY", "5"))

METRICS = {"found": 0, "skipped": 0, "errors": 0}

POST_OK_RE = re.compile(r"201 POST https://api\.hh\.ru/negotiations")
LIMIT_RE = re.compile(r"Лимит откликов|Достигли лимита на отклики")
BLOCK_RE = re.compile(r"429 POST https://api\.hh\.ru/negotiations", re.I)


def report(total_sent, passes_done, stop_reason):
    """Машиночитаемый хвост прогона — его разбирает _summary.py."""
    print(f"Отправлено: {total_sent}", flush=True)
    print(f"PASSES: {passes_done}", flush=True)
    print(f"STOP_REASON: {stop_reason}", flush=True)
    for key, value in METRICS.items():
        print(f"{key.upper()}: {value}", flush=True)
    try:
        from run_history import append
        import datetime
        pid, profile = profiles_lib.get_active_profile()
        append({"time":datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "profile":pid, "account":profile.get("hh_account","main"),
                "sent":total_sent,"reason":stop_reason, **METRICS})
    except (OSError, ValueError):
        print("Could not persist run history", file=sys.stderr)


def run_pass(args):
    """Один проход: (отправлено, лимит, блокировка HH, код)."""
    sent = 0
    limit_reached = False
    blocked = False
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", bufsize=1)
    except FileNotFoundError:
        print(f"ERROR: {args[0]} not found", file=sys.stderr)
        return 0, False, False, 127

    try:
        for line in proc.stdout:
            # Вывод отдаём как есть: его читают tee, _summary.py и watcher.
            sys.stdout.write(redact(line))
            sys.stdout.flush()
            found = re.search(r"Количество вакансий:\s*(\d+)", line)
            if found: METRICS['found'] = max(METRICS['found'], int(found.group(1)))
            if re.search(r"Пропуска[ею]|попала под фильтр|отклонена AI", line): METRICS['skipped'] += 1
            if re.search(r"\[E\]| - ERROR - ", line): METRICS['errors'] += 1
            if line.startswith('CORE_METRICS: '):
                counts = json.loads(line.split(': ', 1)[1])
                for key in METRICS: METRICS[key] = counts[key]
                sent = counts['sent']
            if POST_OK_RE.search(line):
                sent += 1
            if LIMIT_RE.search(line):
                limit_reached = True
            if BLOCK_RE.search(line):
                blocked = True
                Path(PAUSE_FLAG).parent.mkdir(parents=True, exist_ok=True)
                Path(PAUSE_FLAG).touch()
                proc.terminate()
                break
    finally:
        proc.stdout.close()
        try:
            rc = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill(); rc = proc.wait()
    return sent, limit_reached, blocked, rc


def main():
    if os.environ.get('APPLY_SCHEDULED') == '1':
        from schedule_config import due
        if not due(): return 0
    pid, profile = profiles_lib.get_active_profile()
    if not profile:
        print("ERROR: no active profile", file=sys.stderr)
        return 1

    hh_account = profile.get("hh_account", "main")
    search = profile.get("search", "")
    letter = profile.get("letter", "letter_default.txt")
    area = str(profile.get("area", 113))
    excluded = profile.get("excluded_filter", "")
    resume_id = (profile.get("resume_id") or "").strip()

    display = profile.get("display_name") or pid
    print(f"Profile: {display} (HH account: {hh_account})")
    print(f"Search: {search}")
    print(f"Letter: {letter}, Area: {area}")

    letter_path = os.path.join(LETTERS_DIR, os.path.basename(letter))
    if not os.path.isfile(letter_path):
        print(f"ERROR: letter file not found: {letter_path}", file=sys.stderr)
        return 1

    if not search.strip():
        print("ERROR: empty search query", file=sys.stderr)
        return 1

    # Каждый аргумент — отдельный элемент списка, никакого word-splitting.
    ai = profiles_lib.get_ai(profile)
    use_ai = ai['enabled'] or bool(ai['filter'])
    overlay = {'general.cover_letter': open(letter_path, encoding='utf-8').read()}
    if ai['message_prompt']:
        overlay['general.cover_letter_prompt'] = ai['message_prompt']
    if ai['filter_prompt']:
        overlay['general.ai_filter_prompt'] = ai['filter_prompt']
    if ai['system_prompt']:
        overlay['openai.cover_letter.system_prompt'] = ai['system_prompt']
    for section in ('default', 'cover_letter', 'vacancy_filter', 'test_solver'):
        overlay[f'openai.{section}.delay'] = 60 / ai['rate_limit']
        overlay[f'openai.{section}.max_retries'] = 2
    os.environ['HH_BOT_OVERLAY'] = json.dumps(overlay)
    os.environ['HH_BOT_CAPTCHA_UI'] = '1'
    args = cli_args(hh_account, '--use-ai' if use_ai else '--no-use-ai',
                    '-vv', 'apply-vacancies', '--force-cover-letter', '--search', search, '--area', area)

    if resume_id:
        args.extend(["--resume-id", resume_id])

    # excluded_filter: утилита принимает ОДНУ строку через запятую
    # (как в исходной рабочей версии: --excluded-filter "a,b,c").
    excluded = (excluded or "").strip()
    if excluded:
        args.extend(["--excluded-filter", "|".join(re.escape(term.strip()) for term in excluded.split(",") if term.strip())])

    # --- Фильтры поиска и поведение рассылки ---------------------------
    f = profiles_lib.get_filters(profile)

    max_responses = max(1, f["max_responses"])
    args.extend(["--max-responses", str(max_responses)])
    if f["experience"]:
        args.extend(["--experience", f["experience"]])
    if f["schedule"]:
        args.extend(["--schedule", f["schedule"]])
    if f["work_format"]:
        args.append("--work-format")
        args.extend(f["work_format"])
    if f["salary"]:
        args.extend(["--salary", str(f["salary"])])
    if f["only_with_salary"]:
        args.append("--only-with-salary")
    if f["period"]:
        args.extend(["--period", str(f["period"])])
    if f["order_by"]:
        args.extend(["--order-by", f["order_by"]])
    if f["skip_tests"]:
        args.append("--skip-tests")
    if f["send_email"]:
        args.append("--send-email")
    if f["excluded_employer_ids"]:
        args.append("--excluded-employer-id")
        args.extend(f["excluded_employer_ids"])

    # --- AI (1.8.26) ---------------------------------------------------
    ai = profiles_lib.get_ai(profile)

    # AI is a global CLI option and must precede the operation name.
    # Prompts are read from config.toml in upstream 2.5.x.

    if ai["filter"]:
        # Режим custom без промпта утилита примет, но фильтровать будет нечем,
        # поэтому падаем до отправки первого отклика, а не после.
        if ai["filter"] == "custom" and not ai["filter_prompt"].strip():
            print("ERROR: ai.filter=custom requires ai.filter_prompt", file=sys.stderr)
            return 1
        args.append("--ai-filter")

    # Rate limiting belongs in 2.5.x config.toml.

    if ai["dry_run"]:
        args.append("--dry-run")

    if ai["enabled"] or ai["filter"]:
        print(f"AI: messages={ai['enabled']}, filter={ai['filter'] or 'off'}, "
              f"rate_limit={ai['rate_limit']}, dry_run={ai['dry_run']}")

    print(f"Exec: {args}", flush=True)

    # Тестовый прогон повторять бессмысленно: отклики не отправляются, и любой
    # следующий проход выдал бы ровно тот же список.
    max_passes = 1  # Upstream paginates and enforces max_responses in one run.
    total_sent = 0
    passes_done = 0
    stop_reason = "completed"

    # Ручной запуск из бота идёт с APPLY_IGNORE_PAUSE=1: пауза останавливает
    # расписание, но не явное нажатие «Старт».
    honor_pause = os.environ.get("APPLY_IGNORE_PAUSE", "") != "1"

    for attempt in range(1, max_passes + 1):
        if honor_pause and os.path.exists(PAUSE_FLAG):
            print("Pause flag detected, stopping between passes", flush=True)
            stop_reason = "paused"
            break
        if max_passes > 1:
            print(f"--- Проход {attempt} из {max_passes} ---", flush=True)

        sent_this_pass, limit_reached, blocked, rc = run_pass(args)
        total_sent += sent_this_pass
        passes_done = attempt
        print(f"--- Проход {attempt}: отправлено {sent_this_pass}, всего {total_sent} ---",
              flush=True)

        if blocked:
            print("HH требует проверку или ограничил запросы; прекращаем повторные проходы", flush=True)
            report(total_sent, passes_done, "hh_verification_required")
            return 2
        if rc != 0:
            print(f"Утилита завершилась с кодом {rc}, дальше не идём", flush=True)
            reason = {4:'captcha_timeout', 5:'captcha_cancelled', 6:'captcha_retry_limit'}.get(rc, f"tool_error:{rc}")
            report(total_sent, passes_done, reason)
            return rc
        if limit_reached:
            print("Достигнут суточный лимит откликов", flush=True)
            stop_reason = "daily_limit"
            break
        if sent_this_pass == 0:
            print("Новых подходящих вакансий не осталось", flush=True)
            stop_reason = "no_new_vacancies"
            break
        if attempt < max_passes:
            time.sleep(PASS_DELAY)

    if ai["dry_run"]:
        stop_reason = "dry_run"
    report(total_sent, passes_done, stop_reason)
    return 0


if __name__ == "__main__":
    try:
        with file_lock(os.path.join(os.environ.get('CONFIG_DIR', '/app/config'), '.apply.lock'), blocking=False):
            sys.exit(main())
    except (BlockingIOError, PermissionError, OSError) as exc:
        print('Application run unavailable: another run is active or storage is not writable', file=sys.stderr)
        sys.exit(3)
