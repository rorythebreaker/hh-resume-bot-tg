#!/usr/bin/env python3
"""Шлёт в Telegram сводку прогона apply-vacancies, считая по log.txt активного профиля."""
import os
import re
import sys
import urllib.request
import urllib.parse
from datetime import datetime
from pathlib import Path

# Добавляем /app в путь для импорта profiles_lib
sys.path.insert(0, "/app")

TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT_ID", "")
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/app/config"))
LOG_FILE = os.environ.get("LOG_FILE", "")
RC = int(os.environ.get("RC", "0"))
RUN_START = os.environ.get("RUN_START", "")  # "YYYY-MM-DD HH:MM:SS"

if not (TG_TOKEN and TG_CHAT):
    raise SystemExit(0)

# Определяем активный профиль и его лог-файл (config/<hh_account>/log.txt)
profile_display = ""
tool_log_path = CONFIG_DIR / "log.txt"  # fallback на старый путь
try:
    import profiles_lib
    pid, profile = profiles_lib.get_active_profile()
    if profile:
        profile_display = __import__("html").escape(profile.get("display_name") or pid)
        hh_account = profile.get("hh_account", "main")
        candidate = CONFIG_DIR / hh_account / "log.txt"
        if candidate.exists():
            tool_log_path = candidate
except Exception as e:
    print(f"profiles_lib load error: {e}")

def filter_current_run(text, run_start):
    """Оставляет только строки лога с таймстампом >= run_start.
    Строки-продолжения (без таймстампа) наследуют решение предыдущей строки.
    Формат таймстампа в логе утилиты: 'YYYY-MM-DD HH:MM:SS,mmm - ...'."""
    if not run_start:
        return text
    ts_re = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
    out = []
    keeping = False
    for line in text.splitlines():
        m = ts_re.match(line)
        if m:
            keeping = m.group(1) >= run_start
        if keeping:
            out.append(line)
    return "\n".join(out)


# Источник для подсчёта:
# Утилита пишет DEBUG-строки ("201 POST", "Пробуем откликнуться") в log.txt
# независимо от verbosity stdout. Читаем log.txt и фильтруем по времени старта,
# чтобы считать ТОЛЬКО текущий прогон, а не историю.
log = ""
if not LOG_FILE and RUN_START and tool_log_path.exists():
    try:
        with tool_log_path.open("r", errors="ignore") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 500_000))  # последние 500 КБ с запасом
            raw = f.read()
        log = filter_current_run(raw, RUN_START)
    except Exception:
        log = ""

# Фолбэк: если RUN_START не задан или log.txt недоступен — берём stdout прогона
if not log and LOG_FILE:
    try:
        log = Path(LOG_FILE).read_text(errors="ignore")
    except Exception:
        log = ""

# STOP_REASON и PASSES печатает run-apply.py в stdout — в log.txt утилиты их
# нет, поэтому маркеры читаем из перехваченного вывода отдельно от счётчиков.
runner_out = ""
if LOG_FILE:
    try:
        runner_out = Path(LOG_FILE).read_text(errors="ignore")
    except Exception:
        runner_out = ""

# Реально отправленные (по сообщению "Отправлено: N" — итог утилиты)
sent_match_list = re.findall(r"Отправлено:\s*(\d+)", log)
sent = int(sent_match_list[-1]) if sent_match_list else 0

# Альтернативный счёт — по реальным POST 201 (более точно для текущего прогона)
posts_ok = len(re.findall(r"201 POST https://api\.hh\.ru/negotiations", log))
# Берём максимум — что точнее
sent = max(sent, posts_ok)

tried = len(re.findall(r"Пробуем откликнуться", log))

# Считаем ошибки, но игнорируем безвредные:
# - BrokenPipeError: артефакт SIGPIPE при завершении pipe (tee), не настоящая ошибка
# - SystemExit, KeyboardInterrupt — штатное завершение
# Остаются: настоящие ERROR из лога утилиты, неожиданные Exception
all_errors = re.findall(r"^.*\[E\]|^.*ERROR|^.*Exception", log, re.MULTILINE)
errors = sum(
    1 for line in all_errors
    if "BrokenPipeError" not in line
    and "Broken pipe" not in line
    and "SystemExit" not in line
    and "KeyboardInterrupt" not in line
)

# Причина остановки и число проходов — их печатает run-apply.py в конце
STOP_REASONS = {
    "no_new_vacancies": ("🔚", "Новые подходящие вакансии закончились."),
    "daily_limit": ("🛑", "Упёрлись в суточный лимит HH (200/сутки)."),
    "paused": ("⏸", "Остановлено паузой."),
    "completed": ("?", "Прогон завершён с заданным ограничением числа откликов."),
    "hh_verification_required": ("STOP", "Остановлено: HH требует проверку или ограничил доступ."),
    "max_passes": ("🔁", "Исчерпан потолок проходов (APPLY_MAX_PASSES). "
                         "Лимит ещё есть — стоит запустить снова."),
    "dry_run": ("🧪", "Тестовый прогон: отклики не отправлялись."),
}
markers = runner_out or log
stop_match = re.findall(r"^STOP_REASON:\s*(\S+)", markers, re.MULTILINE)
stop_reason = stop_match[-1] if stop_match else ""
passes_match = re.findall(r"^PASSES:\s*(\d+)", markers, re.MULTILINE)
passes_done = int(passes_match[-1]) if passes_match else 0

if stop_reason.startswith("tool_error"):
    code = stop_reason.split(":", 1)[-1]
    stop_icon, stop_text = "❌", f"Утилита завершилась с кодом {code} — прогон прерван."
else:
    stop_icon, stop_text = STOP_REASONS.get(stop_reason, ("", ""))

# Детектим исчерпание лимита HH (дневной лимит ~200 откликов)
limit_hit = bool(re.search(r"Лимит откликов.*исчерпан|Достигли лимита на отклики", log))

if limit_hit:
    icon = "🛑"
    status_line = "<b>Лимит HH исчерпан</b> (200/сутки)\nДождись сброса окна (~24ч)."
elif RC == 0 and errors == 0:
    icon = "✅"
    status_line = None
elif errors > 0:
    icon = "⚠️"
    status_line = None
else:
    icon = "❌"
    status_line = None

lines = [
    "<b>Сводка прогона:</b>",
]
if profile_display:
    lines.append(f"👤 Профиль: <b>{profile_display}</b>")
lines.extend([
    f"📤 Отправлено: <b>{sent}</b>",
    f"🔍 Попыток: {tried}",
    f"🐛 Ошибок: {errors}",
] + ([f"🔁 Проходов: {passes_done}"] if passes_done > 1 else []) + [
    f"Exit: {RC}",
    f"🕒 Сейчас: {datetime.now().strftime('%Y-%m-%d %H:%M')} МСК",
])
if stop_text:
    lines.append("")
    lines.append(f"{stop_icon} {stop_text}")
if status_line and stop_reason != "daily_limit":
    lines.append("")
    lines.append(status_line)

msg = "\n".join(lines)

if os.environ.get("BOT_TRANSPORT", "telegram").strip().lower() == "vk":
    try:
        sys.path.insert(0, "/app")
        import vk_transport
        vk = vk_transport.VKTransport()
        vk.send_text(vk.peer_id, msg)
    except Exception as e:
        print(f"vk summary failed: {e}")
    sys.exit(0)

try:
    data = urllib.parse.urlencode({
        "chat_id": TG_CHAT,
        "text": msg,
        "parse_mode": "HTML",
    }).encode()
    urllib.request.urlopen(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data=data, timeout=15
    ).read()
except Exception as e:
    print(f"tg summary failed: {e}")
