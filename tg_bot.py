#!/usr/bin/env python3
"""
HH-applicant Telegram bot.

Управление полностью кнопочное (нижняя reply-клавиатура с под-меню):
  Корень:     📊 Просмотр | 🎛 Управление | 👤 Профиль | ❓ Справка
  Просмотр:   статус, лимит, статистика, последние отклики, статусы на HH, лог
  Управление: запустить, пауза, возобновить, удалить отказы
  Профиль:    письмо/запрос (показать/изменить), переименовать, регион,
              авторизовать HH, сменить/создать/удалить профиль, список

Набираемые команды: только /start и /menu (возврат к корневой клавиатуре).
"""
import os
import sys
import time
import re
import html
import json
import base64
import sqlite3
import datetime as dt
import random
import threading
import subprocess
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from collections import Counter

# Добавляем /app в путь для импорта profiles_lib
sys.path.insert(0, str(Path(__file__).resolve().parent))
import profiles_lib
from hh_compat import read_access_token, token_path, read_profile_config, profile_config_path, cli_args
from security import redact
import hh_api
import manual_flow
from html_bubbles import HtmlBubbles
import mini_app
import mini_app_context

TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
MINI_APP_URL = os.environ.get('TG_MINI_APP_URL', '').strip()
if MINI_APP_URL:
    MINI_APP_URL = mini_app.public_url(MINI_APP_URL)
MINI_APP_RUNTIME = None
# Во VK-режиме токен Telegram не нужен: адресат берётся из VK_PEER_ID.
TG_CHAT = str(os.environ.get("TG_CHAT_ID") or os.environ.get("VK_PEER_ID") or "")

CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/app/config"))
APP_DIR = Path(os.environ.get("APP_DIR", Path(__file__).resolve().parent))
LETTERS_DIR = APP_DIR / "letters"

PAUSE_FLAG = CONFIG_DIR / ".paused"
PID_FILE = CONFIG_DIR / ".apply.pid"
APPLY_SCRIPT = APP_DIR / "apply-vacancies.sh"
APPLY_WITH_NOTIFY = APP_DIR / "apply_with_notify.sh"

# Файлы для коммуникации с auth_helper в основном контейнере
AUTH_REQ = CONFIG_DIR / ".auth_request"
AUTH_OUT = CONFIG_DIR / ".auth_output"
AUTH_IN = CONFIG_DIR / ".auth_input"
AUTH_STATUS = CONFIG_DIR / ".auth_status"
AUTH_CANCEL = CONFIG_DIR / ".auth_cancel"
AUTH_CAPTCHA = CONFIG_DIR / ".auth_captcha.png"

API = os.environ.get("TG_API_ROOT", "https://api.telegram.org").rstrip("/") + f"/bot{TG_TOKEN}" + ("/test" if os.environ.get("TG_API_TEST_ENV") == "1" else "")

# Контейнер запущен с TZ=Europe/Moscow (см. docker-compose.yml),
# поэтому datetime.now() уже даёт московское время.


def now_msk():
    """Текущее время в Москве (naive datetime, контейнер в TZ=Europe/Moscow)."""
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=3))).replace(tzinfo=None)


def fmt_dt(d, with_msk_label=True):
    """Форматирует datetime в строку с пометкой МСК."""
    s = d.strftime("%Y-%m-%d %H:%M")
    return f"{s} МСК" if with_msk_label else s


# Состояние диалога — какой команды ожидаем ввод
# Возможные значения action:
#   "letter_set"          - заменяем письмо активного профиля
#   "search_set"          - заменяем запрос активного профиля
#   "profile_rename"      - переименовываем display_name активного профиля
#   "area_set"            - меняем регион (area) активного профиля
#   "profile_add:name"    - создаём профиль, шаг 1 (имя)
#   "profile_add:search"  - создаём профиль, шаг 2 (запрос)
#   "auth_input"          - ждём ответа в диалоге авторизации
#   "letter_new:name"     - создаём новый файл письма (имя)
#   "letter_new:text"     - создаём новый файл письма (содержимое)
PENDING = {"action": None, "data": None}

# Выбор резюме активного профиля через меню (inline). Хранит список для callback.
RESUME_PICK = {"pid": None, "resumes": None, "message_id": None}

# Состояние диалога авторизации (в памяти, не персистится)
AUTH_DIALOG = {
    "active": False,        # идёт ли сейчас auth
    "profile_id": None,     # внутренний id профиля
    "hh_account": None,     # для какого hh_account
    "started_at": None,
    "last_prompt_msg_id": None,
}
AUTH_LOCK = threading.Lock()

CRON_FILE = APP_DIR / "crontab"

# Дневной лимит HH (отклики за сутки)
HH_DAILY_LIMIT = 200

STATE_NAMES = {
    "response": "📨 Отклик отправлен",
    "invitation": "🎯 Приглашение",
    "discard": "❌ Отказ",
    "discard_no_appropriate": "❌ Отказ (не подходит)",
    "discard_by_employer": "❌ Отказ от работодателя",
    "discard_by_applicant": "↩️ Отозван вами",
    "discard_to_other_vacancy": "↪️ Переведён на другую вакансию",
    "discard_after_interview": "❌ Отказ после собеседования",
    "hidden": "🙈 Скрыт",
    "archived": "📦 Архив",
    "applicant_not_relevant": "❌ Не релевантен",
    "phone_interview": "📞 Телефонное интервью",
    "assessment": "📝 Тестовое задание",
    "offer": "🎉 Оффер",
}


# =================== Транспорт (Telegram / VK) ===================
# BOT_TRANSPORT=vk переводит бота во ВКонтакте: вся логика меню и команд
# остаётся общей, меняются только отправка, приём и клавиатуры.
TRANSPORT = os.environ.get("BOT_TRANSPORT", "telegram").strip().lower()
VK = None
if TRANSPORT == "vk":
    import vk_transport
    VK = vk_transport.VKTransport()
    print("Transport: VK", flush=True)


# =================== Telegram API ===================

def api_call(method, **params):
    url = f"{API}/{method}"
    data = urllib.parse.urlencode(params).encode() if params else None
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=70) as r:
        return json.loads(r.read())


# Probe the configured Bot API only; never switch servers automatically.
HTML_BUBBLES = HtmlBubbles(lambda method, **params: api_call(method, **params),
                          mode=os.environ.get('TG_HTML_BUBBLES', 'auto').strip().lower())
_CLASSIC_CHATS = set()

def handle_widget_query(query):
    HTML_BUBBLES.handle_query(
        query, TG_CHAT, profiles_lib.get_active_profile()[0],
        dispatch=handle_callback,
        refresh=lambda chat_id, message_id: send_menu(chat_id, build_menu_main(), edit_message_id=message_id))


def send(chat_id, text, parse_mode="HTML"):
    if mini_app_context.text(text): return
    if VK:
        return VK.send_text(chat_id, text)
    text = redact(text)
    MAX = 3900
    while text:
        chunk, text = text[:MAX], text[MAX:]
        try:
            api_call("sendMessage", chat_id=chat_id, text=chunk,
                     parse_mode=parse_mode, disable_web_page_preview="true")
        except Exception as e:
            print(f"send failed: {e}", file=sys.stderr)


def send_plain(chat_id, text):
    if mini_app_context.text(text): return
    """Без HTML/Markdown — для отображения файлов с спецсимволами."""
    if VK:
        return VK.send_text(chat_id, text)
    text = redact(text)
    MAX = 3900
    while text:
        chunk, text = text[:MAX], text[MAX:]
        try:
            api_call("sendMessage", chat_id=chat_id, text=chunk,
                     disable_web_page_preview="true")
        except Exception as e:
            print(f"send failed: {e}", file=sys.stderr)


def _tg_upload(method, field, data_bytes, chat_id, caption, filename, ctype):
    """Загрузка файла из памяти в Telegram (multipart/form-data)."""
    boundary = "----hhbot" + os.urandom(8).hex()
    b = boundary.encode()
    parts = [
        b"--" + b + b"\r\n",
        b'Content-Disposition: form-data; name="chat_id"\r\n\r\n',
        str(chat_id).encode() + b"\r\n",
    ]
    if caption:
        parts += [
            b"--" + b + b"\r\n",
            b'Content-Disposition: form-data; name="caption"\r\n\r\n',
            caption.encode("utf-8") + b"\r\n",
        ]
    parts += [
        b"--" + b + b"\r\n",
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode(),
        f"Content-Type: {ctype}\r\n\r\n".encode(),
        data_bytes + b"\r\n",
        b"--" + b + b"--\r\n",
    ]
    body = b"".join(parts)
    req = urllib.request.Request(f"{API}/{method}", data=body)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"{method} failed: {e}", file=sys.stderr)
        return None


def send_captcha_image(chat_id, png_bytes, caption=""):
    mini_app_context.captcha(png_bytes)
    if VK:
        # Картинкой, а не документом: VK показывает фото сразу в ленте чата.
        return VK.send_file(chat_id, png_bytes, "captcha.png", caption, as_photo=True)
    """Шлёт PNG капчи файлом (документом): Telegram не сжимает документы,
    мелкий текст капчи остаётся чётким. Если sendDocument не прошёл —
    пробуем как фото (на крайний случай)."""
    resp = _tg_upload("sendDocument", "document", png_bytes, chat_id, caption,
                      "captcha.png", "image/png")
    if resp and resp.get("ok"):
        return resp
    return _tg_upload("sendPhoto", "photo", png_bytes, chat_id, caption,
                      "captcha.png", "image/png")


# =================== Нижние reply-клавиатуры ===================

# Нижняя постоянная reply-панель: 5 главных кнопок в один ряд.
# «Пауза» и «Возобновить» — одна кнопка-переключатель: показываем то действие,
# которое сейчас имеет смысл. Панель перерисовывается после каждого нажатия.
def quick_panel():
    toggle = "▶️ Возобновить" if is_paused() else "⏸ Пауза"
    return ["🎛 Меню", "▶️ Старт", toggle, "👁 Отслеживаемые"]


QUICK_LABELS = {"🎛 Меню", "▶️ Старт", "⏸ Пауза", "▶️ Возобновить", "👁 Отслеживаемые"}


def set_panel(chat_id, text):
    """Ставит/обновляет нижнюю reply-панель."""
    if mini_app_context.current():
        mini_app_context.text(text)
        return
    if MINI_APP_URL and not VK:
        return open_mini_app(chat_id, text)
    if VK:
        return VK.set_panel(chat_id, text, quick_panel())
    try:
        api_call(
            "sendMessage", chat_id=chat_id, text=text,
            parse_mode="HTML", disable_web_page_preview="true",
            reply_markup=json.dumps({
                "keyboard": [[{"text": b} for b in quick_panel()]],
                "resize_keyboard": True,
                "is_persistent": True,
            }),
        )
    except Exception as e:
        print(f"set_panel failed: {e}", file=sys.stderr)


def tg_delete(chat_id, msg_id):
    """Удаляет сообщение (нажатие нижней кнопки). Ошибки игнорируем."""
    if mini_app_context.current(): return
    if not msg_id:
        return
    if VK:
        return VK.delete_message(chat_id, msg_id)
    try:
        api_call("deleteMessage", chat_id=chat_id, message_id=msg_id)
    except Exception:
        pass


# =================== Утилиты ===================

def active_hh_account():
    """Возвращает hh_account активного профиля (или 'main' по умолчанию)."""
    pid, profile = profiles_lib.get_active_profile()
    if profile:
        return profile.get("hh_account", "main")
    return "main"


def active_config_dir():
    return CONFIG_DIR / active_hh_account()


def find_db():
    folder = active_config_dir()
    for name in ('data', 'data.db'):
        candidate = folder / name
        if candidate.is_file(): return candidate
    return None


def db():
    db_path = find_db()
    if db_path is None:
        raise FileNotFoundError("Database not found")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def state_label(s):
    return STATE_NAMES.get(s, f"❓ {s}" if s else "—")


def is_paused():
    return PAUSE_FLAG.exists()


def _read_token_for_account():
    """Read access token from current or legacy profile storage."""
    account_dir = active_config_dir()
    return read_access_token(CONFIG_DIR, active_hh_account())


HHAPIError = hh_api.HHAPIError


def hh_error_message(status, payload=None):
    return hh_api.error_message(status, payload)


def hh_get_strict(path):
    return hh_api.request(active_hh_account(), path)


def hh_get(path):
    try:
        return hh_get_strict(path)
    except HHAPIError as exc:
        print(str(exc), file=sys.stderr)
        return {}


def fetch_resumes_for_account(hh_account):
    """[(id, title), ...] for the given HH account."""
    token = read_access_token(CONFIG_DIR, hh_account)
    if not token:
        return []
    req = urllib.request.Request(
        "https://api.hh.ru/resumes/mine",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "hh-applicant-tool/bot",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read()) or {}
    except Exception as e:
        print(f"resumes fetch failed: {e}", file=sys.stderr)
        return []
    out = []
    for it in data.get("items", []):
        rid = it.get("id")
        if rid:
            out.append((rid, it.get("title") or "(без названия)"))
    return out


def read_search_for_active():
    """Возвращает поисковый запрос активного профиля."""
    pid, profile = profiles_lib.get_active_profile()
    if not profile:
        return ""
    return profile.get("search", "")


def write_search_for_active(new_search):
    """Сохраняет новый поисковый запрос для активного профиля."""
    if "'" in new_search:
        raise ValueError("Поисковый запрос не должен содержать одиночные кавычки (')")

    pid, profile = profiles_lib.get_active_profile()
    if not pid:
        raise RuntimeError("Нет активного профиля")
    profiles_lib.update_profile(pid, search=new_search)


def get_active_letter_path():
    """Возвращает путь к файлу письма активного профиля."""
    pid, profile = profiles_lib.get_active_profile()
    if not profile:
        return None
    return profiles_lib.get_letter_path(profile)


def get_active_tool_log():
    from hh_compat import tool_log_path
    return tool_log_path(CONFIG_DIR,active_hh_account())


# =================== Команды ===================

def cmd_help():
    return (
        "🎛 <b>Управление ботом</b>\n\n"
        "<b>Нижняя панель (всегда внизу):</b>\n"
        "• 🎛 Меню — открыть полное меню\n"
        "• ▶️ Старт — запустить рассылку сейчас\n"
        "• ⏸ Пауза / ▶️ Возобновить — пауза рассылки\n"
        "• 👁 Отслеживаемые — статусы выбранных вакансий\n\n"
        "<b>В «🎛 Меню»:</b>\n"
        "• 📊 Просмотр — статус, лимит, статистика, последние 10, статусы на HH, лог\n"
        "• 🎛 Управление — запустить, пауза, возобновить, удалить отказы\n"
        "• 👤 Профиль — письмо и запрос, переименовать, регион, авторизовать HH, "
        "сменить/создать/удалить профиль, список\n\n"
        "Ввод текста (письмо/запрос/имя/регион) отменяется командой /cancel."
    )


def cmd_status():
    """Статус системы. Новый порядок:
    - Заголовок
    - Свободно сейчас
    - Сегодняшний день
    - Текущее время
    - Активность/пауза
    - Сводка за 24ч одной фразой
    - Лимит-блок (если близко к лимиту)
    - Размер лога
    """
    try:
        items = parse_log_applies()
    except Exception as e:
        return f"⚠️ Ошибка чтения лога: {html.escape(str(e))}"

    now = now_msk()
    st = compute_limits_state(items, now)

    lines = ["<b>⚙️ Статус системы</b>"]
    lines.append("")
    lines.append(f"Свободно сейчас: <b>{st['free_now']}</b>")
    lines.append("")
    today_str = now.strftime("%d.%m")
    today_word = plural_ru(st["calendar_today"], ("отклик", "отклика", "откликов"))
    lines.append(f"За сегодняшний день ({today_str}): <b>{st['calendar_today']}</b> {today_word}")
    lines.append("")
    lines.append(f"🕒 Сейчас: <b>{fmt_dt(now)}</b>")
    lines.append(f"Отклики: {'⏸ ПАУЗА' if is_paused() else '▶️ АКТИВНЫ'}")

    if PENDING["action"]:
        lines.append(f"Ожидание ввода: <code>{PENDING['action']}</code> (/cancel — отменить)")

    lines.append(f"За последние 24ч было сделано <b>{st['in_window']}</b> из {HH_DAILY_LIMIT} откликов.")

    return "\n".join(lines)


def cmd_pause():
    try:
        PAUSE_FLAG.parent.mkdir(parents=True, exist_ok=True)
        PAUSE_FLAG.touch()
        was_running = PID_FILE.exists()
        msg = "⏸ <b>Отклики приостановлены.</b>\nЗапланированные задачи будут пропускаться."
        if was_running:
            msg += "\n💥 Активный прогон будет прерван в течение ~5 секунд."
        msg += "\nВозобновить — кнопка «▶️ Возобновить»"
        return msg
    except Exception as e:
        return f"⚠️ Ошибка: {html.escape(str(e))}"


def cmd_resume():
    try:
        if PAUSE_FLAG.exists():
            PAUSE_FLAG.unlink()
        return "▶️ <b>Отклики возобновлены.</b>"
    except Exception as e:
        return f"⚠️ Ошибка: {html.escape(str(e))}"


_RUN_CONFIRM = {}

def cmd_run(chat_id):
    import secrets
    pid, profile = profiles_lib.get_active_profile()
    if not profile: return 'Нет активного профиля.'
    token = secrets.token_hex(8)
    _RUN_CONFIRM[str(chat_id)] = (token, pid, time.monotonic() + 300)
    send_menu(chat_id, {'title': 'Запустить массовые отклики для профиля ' + html.escape(profile.get('display_name', pid)) + '?',
                        'rows': [[('Запустить', 'runconfirm:' + token), ('Отмена', 'runcancel:' + token)]]})
    return None

def _cmd_run_confirmed(chat_id):
    """Запускает apply_with_notify.sh в фоне, ничего не блокируя.

    Пауза останавливает расписание, но не ручной запуск: нажатие «Старт» —
    это явное намерение, и требовать перед ним «Возобновить» незачем."""
    paused = is_paused()
    pid, profile = profiles_lib.get_active_profile()
    run_env = {**os.environ, 'HH_BOT_PROFILE': json.dumps([pid, profile]), 'APPLY_IGNORE_PAUSE': '1', 'APPLY_SCHEDULED': '0'}

    def runner():
        try:
            send(chat_id, "🚀 Запускаю рассылку...")
            env = run_env
            res = subprocess.run(
                ["/bin/bash", str(APPLY_WITH_NOTIFY)],
                capture_output=True, text=True, timeout=900, env=env,
            )
            print(f"manual run finished, rc={res.returncode}")
            # Сводка будет отправлена самим apply_with_notify.sh
        except subprocess.TimeoutExpired:
            send(chat_id, "⚠️ Прогон не завершился за 15 минут.")
        except Exception as e:
            send(chat_id, f"⚠️ Ошибка запуска: {html.escape(str(e))}")

    mini_app_context.make_thread(target=runner, daemon=True).start()
    note = ("\n⏸ Расписание остаётся на паузе — запуск разовый."
            if paused else "")
    return f"✅ Запущено в фоне. Сводка придёт по завершении.{note}"


def cmd_letter_show():
    pid, profile = profiles_lib.get_active_profile()
    if not profile:
        return "❌ Нет активного профиля. Создай профиль кнопкой «➕ Создать профиль»."
    letter_path = profiles_lib.get_letter_path(profile)
    if not letter_path.exists():
        return f"❌ Файл письма не найден: {letter_path.name}"
    text = letter_path.read_text()
    display = profile.get("display_name") or pid
    return (
        f"📝 <b>Письмо профиля «{html.escape(display)}»</b>\n"
        f"Файл: <code>{html.escape(letter_path.name)}</code> ({len(text)} симв.)\n\n"
        f"<pre>{html.escape(text)}</pre>"
    )


def cmd_letter_set_prompt():
    pid, profile = profiles_lib.get_active_profile()
    if not profile:
        return "❌ Нет активного профиля."
    letter_path = profiles_lib.get_letter_path(profile)
    PENDING["action"] = "letter_set"
    PENDING["data"] = str(letter_path)
    return (
        f"✏️ Замена письма <code>{html.escape(letter_path.name)}</code>\n"
        "Пришли новый текст письма <b>одним сообщением</b>.\n\n"
        "Доступные плейсхолдеры:\n"
        "• <code>%(vacancy_name)s</code> — название вакансии\n"
        "• <code>%(employer_name)s</code> — компания\n"
        "• <code>%(first_name)s</code>, <code>%(last_name)s</code>\n"
        "• <code>%(email)s</code>, <code>%(phone)s</code>\n"
        "\nСинтаксис рандомизации: <code>{вариант1|вариант2|вариант3}</code>\n"
        "\nОтменить — командой /cancel"
    )


def cmd_letter_save(text):
    try:
        if len(text) < 50:
            return "⚠️ Текст слишком короткий (минимум 50 символов). /cancel — отменить, или пришли заново."
        if len(text) > 5000:
            return "⚠️ Текст слишком длинный (максимум 5000 символов)."

        target_path = Path(PENDING["data"]) if PENDING["data"] else None
        if not target_path:
            return "⚠️ Ошибка: не задан путь файла."

        # letter_default.txt — общий шаблон: правка в нём затронула бы все
        # профили, которые на него ещё смотрят. Отводим профилю свой файл.
        pid, profile = profiles_lib.get_active_profile()
        moved_to = None
        if pid and target_path.name == profiles_lib.DEFAULT_LETTER:
            target_path = target_path.with_name(f"letter_{pid}.txt")
            moved_to = target_path.name

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(text)
        if moved_to:
            profiles_lib.update_profile(pid, letter=moved_to)
        PENDING["action"] = None
        PENDING["data"] = None
        note = f"\nПрофиль переведён на свой файл: <code>{html.escape(moved_to)}</code>" if moved_to else ""
        return (f"✅ Письмо обновлено ({len(text)} симв.): "
                f"<code>{html.escape(target_path.name)}</code>{note}")
    except PermissionError as e:
        return ("⚠️ Нет прав на запись в каталог писем.\n"
                "Бот работает под UID 1000, а каталог принадлежит другому пользователю.\n\n"
                "На сервере:\n<pre>chown -R 1000:1000 letters profiles.json</pre>\n"
                f"<i>{html.escape(str(e))}</i>")
    except Exception as e:
        return f"⚠️ Ошибка сохранения: {html.escape(str(e))}"


def cmd_search_show():
    pid, profile = profiles_lib.get_active_profile()
    if not profile:
        return "❌ Нет активного профиля."
    search = profile.get("search", "")
    if not search:
        return "❌ Поисковый запрос пуст. Задай кнопкой «✏️ Изменить запрос»."
    return f"<pre>{html.escape(search)}</pre>"


def cmd_search_set_prompt():
    pid, profile = profiles_lib.get_active_profile()
    if not profile:
        return "❌ Нет активного профиля."
    display = profile.get("display_name") or pid
    PENDING["action"] = "search_set"
    return (
        f"✏️ Замена запроса профиля «{html.escape(display)}»\n"
        "Пришли новый поисковый запрос <b>одним сообщением</b>.\n\n"
        "Поддерживается язык запросов HH:\n"
        "• <code>AND</code>, <code>OR</code>, <code>NOT</code>\n"
        "• Скобки <code>( )</code>\n"
        "• Кавычки <code>\" \"</code> для фраз\n"
        "\n⚠️ <b>Не используй одиночные кавычки</b> <code>'</code> — только двойные.\n"
        "\nПример: <code>(devops OR sre) AND linux NOT helpdesk</code>\n"
        "\nОтменить — командой /cancel"
    )


def cmd_search_save(text):
    try:
        text = text.strip()
        if len(text) < 5:
            return "⚠️ Запрос слишком короткий."
        if len(text) > 3000:
            return "⚠️ Запрос слишком длинный (максимум 3000)."

        write_search_for_active(text)
        PENDING["action"] = None
        return f"✅ Поисковый запрос обновлён ({len(text)} симв.)"
    except ValueError as e:
        return f"⚠️ {html.escape(str(e))}"
    except Exception as e:
        return f"⚠️ Ошибка сохранения: {html.escape(str(e))}"


def cmd_cancel():
    # Если идёт auth-диалог — отменяем его
    with AUTH_LOCK:
        if AUTH_DIALOG["active"]:
            AUTH_DIALOG["active"] = False
            AUTH_DIALOG["profile_id"] = None
            AUTH_DIALOG["hh_account"] = None
            try:
                AUTH_CANCEL.touch()
            except Exception as e:
                print(f"cancel auth failed: {e}", file=sys.stderr)
            return "❎ Авторизация отменена."

    was = PENDING["action"]
    PENDING["action"] = None
    PENDING["data"] = None
    if was:
        return f"❎ Отменено ожидание: <code>{was}</code>"
    return "Нет активного ожидания ввода."


# =================== Профили ===================

def cmd_profiles():
    """Список всех профилей."""
    items = profiles_lib.list_profiles()
    if not items:
        return "Профилей пока нет. Создай кнопкой «➕ Создать профиль»."
    active_pid, _ = profiles_lib.get_active_profile()
    lines = ["<b>👥 Профили:</b>\n"]
    for pid, p in items:
        marker = "▶️" if pid == active_pid else "  "
        display = p.get("display_name") or pid
        hh_acc = p.get("hh_account", "main")
        search_short = (p.get("search") or "")[:40]
        if len(p.get("search") or "") > 40:
            search_short += "…"
        lines.append(
            f"{marker} <b>{html.escape(display)}</b>\n"
            f"   HH-аккаунт: <code>{html.escape(hh_acc)}</code>\n"
            f"   Запрос: <code>{html.escape(search_short)}</code>"
        )
    return "\n".join(lines)


def cmd_profile_rename_prompt():
    pid, profile = profiles_lib.get_active_profile()
    if not profile:
        return "❌ Нет активного профиля."
    display = profile.get("display_name") or pid
    PENDING["action"] = "profile_rename"
    return (
        f"🏷 Переименование профиля «{html.escape(display)}»\n"
        "Пришли новое имя одним сообщением (1-50 символов).\n\n"
        "Отменить — командой /cancel"
    )


def cmd_profile_rename_save(text):
    text = text.strip()
    if len(text) < 1 or len(text) > 50:
        return "⚠️ Имя должно быть 1-50 символов."
    pid, _ = profiles_lib.get_active_profile()
    if not pid:
        return "❌ Нет активного профиля."
    try:
        profiles_lib.update_profile(pid, display_name=text)
        PENDING["action"] = None
        return f"✅ Профиль переименован: «{html.escape(text)}»"
    except Exception as e:
        return f"⚠️ Ошибка: {html.escape(str(e))}"


def fetch_area_name(area_id):
    """Запрашивает название региона у api.hh.ru/areas/{id}.
    Возвращает кортеж (status, name):
      status="valid"       — код существует, name = название
      status="invalid"     — код не существует (404)
      status="unreachable" — api.hh.ru недоступен/таймаут, name = None
    """
    url = f"https://api.hh.ru/areas/{area_id}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "hh-applicant-tool/bot",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read()) or {}
        return ("valid", data.get("name") or str(area_id))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return ("invalid", None)
        return ("unreachable", None)
    except Exception:
        return ("unreachable", None)


def cmd_area_prompt():
    pid, profile = profiles_lib.get_active_profile()
    if not profile:
        return "❌ Нет активного профиля."
    current = profile.get("area", 113)
    status, name = fetch_area_name(current)
    if status == "valid":
        current_line = f"Текущий регион: <b>{current}</b> — {html.escape(name)}"
    else:
        current_line = f"Текущий регион: <b>{current}</b>"
    PENDING["action"] = "area_set"
    return (
        f"🌍 Смена региона активного профиля.\n"
        f"{current_line}\n\n"
        "Пришли <b>числовой код</b> региона одним сообщением.\n"
        "Примеры: 113 — Россия, 1 — Москва, 2 — СПб.\n"
        "Справочник: https://api.hh.ru/areas\n\n"
        "Отменить — командой /cancel"
    )


def cmd_area_save(text):
    text = text.strip()
    if not text.isdigit():
        return "⚠️ Нужен числовой код региона. Попробуй ещё раз или /cancel."
    area_id = int(text)
    pid, _ = profiles_lib.get_active_profile()
    if not pid:
        return "❌ Нет активного профиля."

    status, name = fetch_area_name(area_id)
    if status == "invalid":
        return f"⚠️ Региона с кодом {area_id} не существует. Попробуй ещё раз или /cancel."

    try:
        profiles_lib.update_profile(pid, area=area_id)
        PENDING["action"] = None
    except Exception as e:
        return f"⚠️ Ошибка: {html.escape(str(e))}"

    if status == "valid":
        return f"✅ Регион изменён: <b>{area_id}</b> — {html.escape(name)}"
    return (
        f"✅ Регион изменён: <b>{area_id}</b>\n"
        "(api.hh.ru недоступен — сохранено без проверки названия)"
    )


def cmd_profile_add_start():
    """Запускает диалог создания профиля."""
    PENDING["action"] = "profile_add:name"
    PENDING["data"] = {}
    return (
        "➕ <b>Создание нового профиля</b>\n\n"
        "Шаг 1/3: Пришли <b>название профиля</b> (как ты его будешь видеть в боте).\n"
        "Например: «DevOps», «Сисадмин Москва», «Второй аккаунт»\n\n"
        "Отменить — командой /cancel"
    )


def cmd_profile_add_step(text):
    """Обработка шагов создания профиля."""
    text = text.strip()
    step = PENDING["action"].split(":", 1)[1]
    data = PENDING.get("data") or {}

    if step == "name":
        if len(text) < 1 or len(text) > 50:
            return "⚠️ Название должно быть 1-50 символов."
        data["display_name"] = text
        PENDING["action"] = "profile_add:search"
        PENDING["data"] = data
        return (
            f"✅ Название: <b>{html.escape(text)}</b>\n\n"
            "Шаг 2/3: Пришли <b>поисковый запрос</b> для HH.\n\n"
            "Поддерживается язык запросов HH:\n"
            "• <code>AND</code>, <code>OR</code>, <code>NOT</code>\n"
            "• Скобки <code>( )</code>\n"
            "• Кавычки <code>\" \"</code> для фраз\n\n"
            "⚠️ Не используй одиночные кавычки <code>'</code>.\n\n"
            "Пример: <code>(devops OR sre) AND linux</code>\n\n"
            "Отменить — командой /cancel"
        )

    if step == "search":
        if "'" in text:
            return "⚠️ Запрос не должен содержать одиночные кавычки <code>'</code>. Пришли заново."
        if len(text) < 5 or len(text) > 3000:
            return "⚠️ Запрос должен быть 5-3000 символов."
        data["search"] = text
        PENDING["action"] = "profile_add:account"
        PENDING["data"] = data

        # Предложим существующие HH-аккаунты
        existing = profiles_lib.get_hh_accounts()
        accounts_hint = ""
        if existing:
            accounts_hint = (
                "\nСуществующие HH-аккаунты в системе: "
                + ", ".join(f"<code>{html.escape(a)}</code>" for a in existing)
            )
        return (
            f"✅ Запрос сохранён ({len(text)} симв.)\n\n"
            "Шаг 3/3: Пришли <b>идентификатор HH-аккаунта</b>.\n\n"
            "Это короткое имя без пробелов, например <code>main</code>, <code>second</code>, <code>backup</code>.\n"
            "Профили с одинаковым идентификатором используют один HH-аккаунт."
            f"{accounts_hint}\n\n"
            "Отменить — командой /cancel"
        )

    if step == "account":
        if not re.match(r"^[a-z0-9_-]{1,30}$", text):
            return "⚠️ Идентификатор: маленькие буквы, цифры, <code>_</code> или <code>-</code>, 1-30 символов."
        data["hh_account"] = text
        PENDING["action"] = None
        PENDING["data"] = None
        return _finalize_profile_creation(data, "")

    return f"⚠️ Неизвестный шаг: {step}"


def _finalize_profile_creation(data, resume_id=""):
    """Создаёт профиль (с пустым письмом и resume_id) и возвращает текст-итог."""
    try:
        new_pid = profiles_lib.next_profile_id()
        letter_name = f"letter_{new_pid}.txt"
        (profiles_lib.LETTERS_DIR / letter_name).write_text("")
        profiles_lib.add_profile(
            display_name=data["display_name"],
            hh_account=data["hh_account"],
            search=data["search"],
            letter=letter_name,
            area=113,
            excluded_filter="",
            resume_id=resume_id,
        )
    except Exception as e:
        return f"⚠️ Ошибка создания: {html.escape(str(e))}"

    account_config = profile_config_path(CONFIG_DIR, data["hh_account"])
    auth_hint = ""
    if not account_config.exists():
        auth_hint = (
            f"\n\n⚠️ HH-аккаунт <code>{html.escape(data['hh_account'])}</code> "
            "ещё не авторизован. Авторизуй кнопкой «🔑 Авторизовать HH»."
        )
    if resume_id:
        resume_line = f"\nРезюме: <code>{html.escape(resume_id)}</code>"
    else:
        resume_line = "\nРезюме: по умолчанию (как выберет утилита)"
    return (
        f"✅ Профиль <b>«{html.escape(data['display_name'])}»</b> создан.\n"
        f"HH-аккаунт: <code>{html.escape(data['hh_account'])}</code>"
        f"{resume_line}\n"
        f"Письмо: <code>{letter_name}</code> (пустое — задай кнопкой «✏️ Изменить письмо»)"
        f"{auth_hint}\n\n"
        "Переключиться: «👤 Профиль» → «⟳ Сменить профиль»."
    )


def cmd_profile_switch(profile_id):
    """Переключает активный профиль."""
    try:
        profiles_lib.set_active_profile(profile_id)
        _, p = profiles_lib.get_active_profile()
        display = p.get("display_name") or profile_id
        return f"✅ Активный профиль: <b>{html.escape(display)}</b>"
    except ValueError:
        return "⚠️ Профиль не найден."
    except Exception as e:
        return f"⚠️ Ошибка: {html.escape(str(e))}"


def cmd_profile_remove(profile_id):
    """Удаляет профиль."""
    items_before = profiles_lib.list_profiles()
    if len(items_before) <= 1:
        return "⚠️ Нельзя удалить последний профиль."
    try:
        # Найдём display_name до удаления
        data = profiles_lib.load_profiles()
        if profile_id not in data["profiles"]:
            return "⚠️ Профиль не найден."
        display = data["profiles"][profile_id].get("display_name") or profile_id
        profiles_lib.remove_profile(profile_id)
        return f"✅ Профиль «{html.escape(display)}» удалён."
    except Exception as e:
        return f"⚠️ Ошибка: {html.escape(str(e))}"


# =================== Авторизация HH через бота ===================

def cmd_auth_start():
    """Запускает диалог авторизации для активного профиля."""
    pid, profile = profiles_lib.get_active_profile()
    if not profile:
        return "❌ Нет активного профиля."

    hh_account = profile.get("hh_account", "main")

    with AUTH_LOCK:
        if AUTH_DIALOG["active"]:
            return (
                f"⚠️ Авторизация уже идёт для аккаунта <code>{html.escape(AUTH_DIALOG['hh_account'])}</code>.\n"
                "Отменить — командой /cancel"
            )
        AUTH_DIALOG["active"] = True
        AUTH_DIALOG["profile_id"] = pid
        AUTH_DIALOG["hh_account"] = hh_account
        AUTH_DIALOG["started_at"] = now_msk()

    # Очищаем старые комм-файлы (включая статус — иначе наблюдатель
    # прочитает прошлую ошибку и сразу завершится).
    for f in (AUTH_OUT, AUTH_IN, AUTH_CANCEL, AUTH_STATUS, AUTH_CAPTCHA):
        try:
            if f.exists():
                f.unlink()
        except Exception:
            pass

    auth_started_ts = time.time()

    # Пишем запрос в auth_helper
    try:
        AUTH_REQ.write_text(json.dumps({
            "profile": hh_account,
            "ts": time.time(),
        }))
    except Exception as e:
        with AUTH_LOCK:
            AUTH_DIALOG["active"] = False
        return f"⚠️ Не удалось запустить: {html.escape(str(e))}"

    # Запускаем поток-наблюдатель за выводом утилиты
    mini_app_context.make_thread(target=_auth_watcher, args=(TG_CHAT, auth_started_ts), daemon=True).start()

    return (
        f"🔄 Запускаю авторизацию для аккаунта <code>{html.escape(hh_account)}</code>...\n\n"
        "⚠️ Будь готов получить SMS-код.\n"
        "Когда утилита спросит — отвечай сообщением в этот чат.\n\n"
        "Отменить — командой /cancel"
    )


def _auth_watcher(chat_id, started_ts=0):
    """Фоновый поток: смотрит за .auth_output и .auth_status,
    пересылает прогресс в TG. Игнорирует статус старше started_ts."""
    start_time = time.time()
    last_msg = ""

    while True:
        with AUTH_LOCK:
            if not AUTH_DIALOG["active"]:
                return

        # Хард-таймаут 10 минут
        if time.time() - start_time > 600:
            with AUTH_LOCK:
                AUTH_DIALOG["active"] = False
            try:
                send(chat_id, "⏱ Авторизация прервана (таймаут 10 минут).")
            except Exception:
                pass
            return

        # Капча: helper кладёт готовый PNG в файл — шлём его картинкой
        if AUTH_CAPTCHA.exists():
            try:
                png = AUTH_CAPTCHA.read_bytes()
                AUTH_CAPTCHA.unlink()
                if png:
                    send_captcha_image(
                        chat_id, png,
                        "🔐 Капча. Введите текст с картинки одним сообщением.")
            except Exception as e:
                print(f"captcha send error: {e}", file=sys.stderr)

        # Читаем текстовый вывод утилиты (base64 капчи сюда уже не попадает)
        if AUTH_OUT.exists():
            try:
                content = AUTH_OUT.read_text()
                AUTH_OUT.unlink()
                content = content.strip()
                if content and content != last_msg:
                    last_msg = content
                    if len(content) > 3500:
                        content = content[:3500] + "…"
                    send(chat_id, f"🤖 <pre>{html.escape(content)}</pre>")
            except Exception as e:
                print(f"_auth_watcher read error: {e}", file=sys.stderr)

        # Проверяем статус
        if AUTH_STATUS.exists():
            try:
                status = json.loads(AUTH_STATUS.read_text())
                # Игнорируем статус, записанный до старта этой авторизации
                if status.get("ts", 0) < started_ts:
                    time.sleep(1)
                    continue
                state = status.get("state")
                msg = status.get("msg", "")
                if state == "done":
                    with AUTH_LOCK:
                        AUTH_DIALOG["active"] = False
                    send(chat_id, f"✅ <b>Авторизация успешна</b>\n{html.escape(msg)}\n\nМожно «▶️ Старт» или подождать cron.")
                    return
                if state == "error":
                    with AUTH_LOCK:
                        AUTH_DIALOG["active"] = False
                    send(chat_id, f"❌ <b>Ошибка авторизации</b>\n<pre>{html.escape(msg)}</pre>")
                    return
            except Exception:
                pass

        time.sleep(1)


def handle_auth_input(text):
    """Когда мы в auth-режиме, любой текст идёт в stdin утилиты."""
    try:
        AUTH_IN.write_text(text)
        return None  # ответ придёт через _auth_watcher
    except Exception as e:
        return f"⚠️ Не удалось передать ввод: {html.escape(str(e))}"


def schedule_menu():
    from schedule_config import read_hours
    return {'title': 'Расписание (МСК): ' + ', '.join(map(str, read_hours())) + '\nЗапуски каждый час с задержкой 1–5 минут.',
            'rows': [[('Изменить часы', 'cmd:/schedule_set')], [('Обновить', 'menu:schedule'), ('Назад', 'menu:ctl')]]}

def schedule_prompt():
    PENDING['action'] = 'schedule_set'
    return 'Введите часы МСК: 8-21 или 8,12,18. /cancel — отмена.'

def history_message():
    from run_history import load
    account = active_hh_account()
    records = [r for r in load() if r.get('account') == account][-10:]
    return '\n'.join(html.escape(f"{r['time']}: {r['profile']}, found={r['found']}, sent={r['sent']}, skipped={r['skipped']}, errors={r['errors']}, {r['reason']}") for r in records) or 'История запусков пуста.'

def parse_apply_cron_hours():
    """Читает crontab, находит строку с apply_with_notify.sh, извлекает
    список часов запуска. Возвращает (hours_list, source_str).
    На случай разбора неудачи — fallback на 8-21."""
    from schedule_config import read_hours
    hours = read_hours()
    return hours, ','.join(map(str,hours))


def expand_cron_hours(field):
    """Раскрывает hour-поле cron в список часов 0-23.
    Поддерживает: '*', '8-21', '8,12,18', '*/4', '8-21/2'."""
    result = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
        else:
            base = part
        if base == "*":
            start, end = 0, 23
        elif "-" in base:
            a, b = base.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(base)
        for h in range(start, end + 1, step):
            if 0 <= h <= 23:
                result.add(h)
    return sorted(result)


def next_cron_run(hours):
    """Возвращает следующий запуск cron как naive datetime (в МСК, т.к.
    контейнер с TZ=Europe/Moscow). Cron запускается в :00 указанных часов."""
    now = now_msk()
    for day_offset in (0, 1):
        day = now.date() + dt.timedelta(days=day_offset)
        for h in hours:
            candidate = dt.datetime.combine(day, dt.time(hour=h, minute=0))
            if candidate > now:
                return candidate
    return None


def plural_ru(n, forms):
    """Выбирает правильную форму существительного для числа n.
    forms = (форма_для_1, форма_для_2_4, форма_для_остальных)
    Пример: plural_ru(5, ('отклик', 'отклика', 'откликов')) → 'откликов'."""
    n = abs(n) % 100
    if 11 <= n <= 14:
        return forms[2]
    n %= 10
    if n == 1:
        return forms[0]
    if 2 <= n <= 4:
        return forms[1]
    return forms[2]


def fmt_timedelta(td):
    """Преобразует timedelta в строку '2ч 15м'."""
    total_sec = int(td.total_seconds())
    if total_sec < 0:
        total_sec = 0
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    if hours > 0:
        return f"{hours}ч {minutes:02d}м"
    return f"{minutes}м"


def fmt_dt_short(d):
    """Короткий формат — только время или 'завтра HH:MM' если завтра."""
    now = now_msk()
    if d.date() == now.date():
        return d.strftime("%H:%M МСК")
    if d.date() == now.date() + dt.timedelta(days=1):
        return d.strftime("завтра %H:%M МСК")
    return d.strftime("%d.%m %H:%M МСК")


def compute_limits_state(items, now):
    """Возвращает структуру с полной информацией о лимитах HH.

    items: список dict(ts, vid) — все отклики из лога
    now: текущее время (naive datetime в МСК)

    Returns:
        dict с полями:
          - in_window: int — отправлено за последние 24 часа
          - free_now: int — свободно слотов сейчас
          - calendar_today: int — отправлено по календарной дате
          - window_start: datetime — начало текущего окна (now - 24ч)
          - oldest_in_window: datetime|None — время самого старого отклика в окне
          - sorted_ts_in_window: list[datetime] — все timestamps в окне (отсортированы)
          - full_reset_at: datetime|None — когда последний отклик «выпадет» из окна
          - free_slot_times: list[(int, datetime)] — пары (через сколько отрисуется N слотов, время)
    """
    window_start = now - dt.timedelta(hours=24)
    in_window_items = [x for x in items if x["ts"] >= window_start]
    sorted_ts = sorted(x["ts"] for x in in_window_items)
    in_window = len(sorted_ts)
    free_now = max(0, HH_DAILY_LIMIT - in_window)
    today = now.date()
    calendar_today = sum(1 for x in items if x["ts"].date() == today)

    oldest_in_window = sorted_ts[0] if sorted_ts else None
    # Полный сброс — когда самый свежий отклик станет старше 24ч
    full_reset_at = (sorted_ts[-1] + dt.timedelta(hours=24)) if sorted_ts else None

    # Когда освободятся первые N слотов: каждый отклик «выпадает» из окна
    # через 24ч после отправки. То есть слот #k освобождается в момент
    # sorted_ts[k-1] + 24h.
    # Если в окне 200, то первый слот откроется в sorted_ts[0]+24h.
    # 10-й слот — в sorted_ts[9]+24h. И так далее.
    free_slot_times = []
    if in_window >= HH_DAILY_LIMIT:
        # Лимит полный — показываем когда откроются 1, 10, 50, все
        anchors = [1, 10, 50, in_window]
    elif free_now < 20:
        # Близко к лимиту — показываем когда откроется ещё несколько
        anchors = [free_now + 1, free_now + 10, in_window]
    else:
        anchors = []

    for n in anchors:
        if n <= 0 or n > in_window:
            continue
        # n-й слот освобождается через 24ч после (n-1)-го отклика в окне
        slot_open_ts = sorted_ts[n - 1] + dt.timedelta(hours=24)
        if slot_open_ts > now:
            free_slot_times.append((n, slot_open_ts))
    # Убираем дубликаты по n
    seen_n = set()
    free_slot_times = [(n, t) for n, t in free_slot_times if not (n in seen_n or seen_n.add(n))]

    return {
        "in_window": in_window,
        "free_now": free_now,
        "calendar_today": calendar_today,
        "window_start": window_start,
        "oldest_in_window": oldest_in_window,
        "sorted_ts_in_window": sorted_ts,
        "full_reset_at": full_reset_at,
        "free_slot_times": free_slot_times,
    }


def predict_slots_at(limits_state, target_time):
    """Сколько слотов будет свободно к моменту target_time.

    Учитывает, что отклики «выпадают» из окна через 24ч после отправки.
    """
    if target_time is None:
        return limits_state["free_now"]
    sorted_ts = limits_state["sorted_ts_in_window"]
    # Те отклики, которые к моменту target_time выйдут за пределы 24ч-окна
    cutoff = target_time - dt.timedelta(hours=24)
    expired_by_target = sum(1 for ts in sorted_ts if ts < cutoff)
    in_window_at_target = len(sorted_ts) - expired_by_target
    return max(0, HH_DAILY_LIMIT - in_window_at_target)


def format_limits_block(items, now, next_cron_at=None):
    """Возвращает текстовый блок с информацией о лимитах HH.
    Один формат — используется и в /status, и в /next."""
    st = compute_limits_state(items, now)

    lines = []
    lines.append("<b>📊 Лимит HH</b> (200 откликов за 24 часа)")
    lines.append("")

    # Основная сводка
    bar_filled = st["in_window"]
    lines.append(f"За последние 24ч: <b>{bar_filled} из {HH_DAILY_LIMIT}</b>")
    lines.append(f"Свободно сейчас: <b>{st['free_now']}</b>")

    today_str = now.strftime("%d.%m")
    today_word = plural_ru(st["calendar_today"], ("отклик", "отклика", "откликов"))
    lines.append(f"За календарный день ({today_str}): {st['calendar_today']} {today_word}")

    # Когда освободятся слоты — показываем только если близко к лимиту
    if st["free_slot_times"]:
        lines.append("")
        lines.append("<b>⏳ Когда освободятся слоты:</b>")
        for n, t in st["free_slot_times"]:
            delta = t - now
            slot_word = plural_ru(n, ("слот", "слота", "слотов"))
            if n == HH_DAILY_LIMIT or (st["in_window"] >= HH_DAILY_LIMIT and n == st["in_window"]):
                label = f"Все {n}"
            else:
                label = f"{n} {slot_word}"
            lines.append(f"  • {label}: через {fmt_timedelta(delta)} ({fmt_dt_short(t)})")

    # Полный сброс окна (когда все 200 будут доступны)
    if st["full_reset_at"] and st["full_reset_at"] > now:
        # Показываем только если ещё не показано в free_slot_times
        already_shown = any(n == st["in_window"] for n, _ in st["free_slot_times"])
        if not already_shown and st["in_window"] >= HH_DAILY_LIMIT * 0.5:
            delta = st["full_reset_at"] - now
            lines.append("")
            lines.append(f"🔄 Полный сброс окна: через {fmt_timedelta(delta)} ({fmt_dt_short(st['full_reset_at'])})")

    # Что будет к следующему cron-прогону
    if next_cron_at:
        slots_at_cron = predict_slots_at(st, next_cron_at)
        lines.append("")
        lines.append(f"🤖 К следующему прогону ({fmt_dt_short(next_cron_at)}):")
        if slots_at_cron > 0:
            slot_word = plural_ru(slots_at_cron, ("слот", "слота", "слотов"))
            lines.append(f"   будет свободно ~<b>{slots_at_cron}</b> {slot_word}")
        else:
            lines.append("   слотов не будет, прогон отработает вхолостую")

    return "\n".join(lines)


def cmd_next():
    """Следующий прогон. Строго по шаблону."""
    hours, source = parse_apply_cron_hours()
    nxt = next_cron_run(hours)

    items = parse_log_applies()
    now = now_msk()
    st = compute_limits_state(items, now)

    lines = []

    # Ближайший cron
    if is_paused():
        lines.append("⏸ <b>Стоит пауза.</b> Запланированные прогоны пропускаются до возобновления.")
    elif nxt:
        delta = nxt - now
        lines.append(f"📅 Ближайший прогон: <b>{fmt_dt(nxt)}</b> (через {fmt_timedelta(delta)})")
    else:
        lines.append("⚠️ Не удалось вычислить следующий прогон.")

    # Прогноз на cron
    if nxt and not is_paused():
        slots_at_cron = predict_slots_at(st, nxt)
        lines.append("")
        lines.append(f"🤖 К следующему прогону ({fmt_dt_short(nxt)}):")
        if slots_at_cron > 0:
            slot_word = plural_ru(slots_at_cron, ("слот", "слота", "слотов"))
            lines.append(f"   будет свободно ~<b>{slots_at_cron}</b> {slot_word}")
        else:
            lines.append("   слотов не будет, прогон отработает вхолостую")

    # Полный сброс окна
    if st["full_reset_at"] and st["full_reset_at"] > now and st["in_window"] > 0:
        delta = st["full_reset_at"] - now
        lines.append("")
        lines.append(f"🔄 Полный сброс всех лимитов через {fmt_timedelta(delta)} ({fmt_dt_short(st['full_reset_at'])})")

    # Текущее время — в самом конце
    lines.append("")
    lines.append(f"🕒 Сейчас: <b>{fmt_dt(now)}</b>")

    return "\n".join(lines)


def cmd_reply_employers(chat_id):
    """`reply-employers`: ответы в чатах с работодателями.

    Два режима. С включённым AI ответ генерируется под каждую переписку
    (--use-ai). Иначе во все чаты уходит готовый шаблон из профиля (-m).
    Без того и другого утилита ждёт текст в интерактивном режиме и из бота
    просто зависла бы — этот случай отсекаем заранее."""
    pid, profile = profiles_lib.get_active_profile()
    if not profile:
        return "❌ Нет активного профиля."
    account = profile.get("hh_account", "main")
    ai = profiles_lib.get_ai(profile)
    reply_message = (profile.get("reply_message") or "").strip()

    args = cli_args(account, "--use-ai" if ai["enabled"] else "--no-use-ai", "reply-chats")
    if ai["enabled"]:
        cfg = read_ai_config(account)
        if not (cfg["api_key"] and cfg["base_url"]):
            return "❌ Не задан ключ или endpoint: «🤖 Настройки AI» → «🔐 Доступ к AI»."
        pass  # Global AI option is already before the operation.
        mode_note = "AI-ответ под каждую переписку"
    elif reply_message:
        args.extend(["-m", reply_message])
        mode_note = "шаблон из профиля"
    else:
        return ("❌ Нечем отвечать. Либо включи «🤖 Настройки AI» → «⚡ Письма», "
                "либо задай текст: «👤 Профиль» → «💬 Ответ работодателям».")
    resume_id = (profile.get("resume_id") or "").strip()
    if resume_id:
        args.extend(["--resume-id", resume_id])
    if ai["dry_run"]:
        args.append("--dry-run")

    def runner():
        try:
            res = subprocess.run(args, capture_output=True, text=True, timeout=900)
            output = ((res.stdout or "") + (res.stderr or ""))[-1500:]
            head = "✅ Ответы отправлены." if res.returncode == 0 else f"⚠️ Код {res.returncode}"
            send(chat_id, f"{head}\n\n<pre>{html.escape(output or 'нет вывода')}</pre>")
        except subprocess.TimeoutExpired:
            send(chat_id, "⚠️ Команда не завершилась за 15 минут.")
        except FileNotFoundError:
            send(chat_id, "⚠️ hh-applicant-tool не найден в PATH.")
        except Exception as e:
            send(chat_id, f"⚠️ Ошибка: {html.escape(str(e))}")

    mini_app_context.make_thread(target=runner, daemon=True).start()
    mode = " (тестовый прогон)" if ai["dry_run"] else ""
    return f"🚀 Отвечаю работодателям{mode}: {mode_note}. Результат пришлю по завершении."


def cmd_cleanup(chat_id):
    """Запускает `hh-applicant-tool clear-negotiations -d`.
    Удаляет переговоры со статусом 'discard' (отказы)."""

    def runner():
        try:
            send(chat_id, "Начато удаление отказов.")
            _, profile = profiles_lib.get_active_profile()
            hh_account = (profile or {}).get("hh_account", "main")
            res = subprocess.run(
                cli_args(hh_account, "clear-negotiations", "--blacklist-discard"),
                capture_output=True, text=True, timeout=300,
            )
            output = (res.stdout or "") + (res.stderr or "")
            tail = output[-1500:] if len(output) > 1500 else output

            if res.returncode == 0:
                msg = f"✅ Удаление отказов завершено.\n\n<pre>{html.escape(tail or 'OK')}</pre>"
            else:
                msg = f"⚠️ Завершено с кодом {res.returncode}\n\n<pre>{html.escape(tail or 'нет вывода')}</pre>"
            send(chat_id, msg)
        except subprocess.TimeoutExpired:
            send(chat_id, "⚠️ Команда не завершилась за 5 минут.")
        except FileNotFoundError:
            send(chat_id, "⚠️ hh-applicant-tool не найден в PATH.\nЭта команда работает только внутри основного контейнера. См. «📜 Лог» для деталей.")
        except Exception as e:
            send(chat_id, f"⚠️ Ошибка: {html.escape(str(e))}")

    mini_app_context.make_thread(target=runner, daemon=True).start()
    return "🚀 Запущено в фоне. Результат пришлёт по завершении."


def cmd_log():
    """Последние 30 строк лога утилиты."""
    log_path = CONFIG_DIR / active_hh_account() / "last-run.log"
    if not log_path.exists():
        log_path = get_active_tool_log()
    if not log_path.exists():
        return "Лог пуст или не создан."
    try:
        with log_path.open("r", errors="ignore") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 30_000))
            tail = f.read()
        import apply_captcha
        captcha_state = apply_captcha.status(profiles_lib.get_active_profile()[0])
        out = "\n".join(tail.splitlines()[-30:])[-3000:]
        if captcha_state:
            out = "CAPTCHA_STATUS: " + captcha_state["message"] + "\n" + captcha_state.get('diagnostics','') + "\n\n" + out
        return f"📜 <b>Хвост лога:</b>\n\n<pre>{html.escape(redact(out))}</pre>"
    except Exception as e:
        return f"⚠️ {html.escape(str(e))}"


def is_hh_limit_hit_recently():
    """Смотрит последние ~50 КБ лога и проверяет, не было ли там
    в последний час сообщения об исчерпании дневного лимита HH.
    Возвращает (hit: bool, ts: datetime or None)."""
    if not get_active_tool_log().exists():
        return (False, None)
    try:
        with get_active_tool_log().open("r", errors="ignore") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 50_000))
            tail = f.read()
    except Exception:
        return (False, None)

    # Ищем последнее упоминание лимита
    pattern = re.compile(
        r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+-\s+\w+\s+-\s+"
        r"(?:Лимит откликов.*исчерпан|Достигли лимита на отклики)",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(tail))
    if not matches:
        return (False, None)
    try:
        ts = dt.datetime.strptime(matches[-1].group(1), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return (True, None)

    # Считаем "недавним" если в последние 12 часов
    if (now_msk() - ts) > dt.timedelta(hours=12):
        return (False, ts)
    return (True, ts)


def parse_log_applies():
    """Парсит config/log.txt и возвращает список всех успешных откликов.
    Каждый элемент: dict(ts: datetime, vid: str).
    Только уникальные vacancy_id (если бот ретраил — учитываем один раз)."""
    if not get_active_tool_log().exists():
        return []

    # Формат строки:
    # 2026-04-23 13:08:15,432 - DEBUG - 201 POST https://api.hh.ru/negotiations with params: {'resume_id': '...', 'vacancy_id': '132405587', ...}
    pattern = re.compile(
        r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+-\s+\w+\s+-\s+"
        r"201 POST https://api\.hh\.ru/negotiations with params:\s*\{[^}]*"
        r"'vacancy_id':\s*'(\d+)'"
    )

    seen = set()
    items = []
    try:
        with get_active_tool_log().open("r", errors="ignore") as f:
            for line in f:
                m = pattern.search(line)
                if not m:
                    continue
                vid = m.group(2)
                if vid in seen:
                    continue
                seen.add(vid)
                try:
                    ts = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue
                items.append({"ts": ts, "vid": vid})
    except Exception as e:
        print(f"parse_log_applies failed: {e}", file=sys.stderr)
    return items


def fetch_vacancy_brief(vid):
    """Краткая инфа по вакансии (name, employer) через HH API.
    Токен берётся для активного HH-аккаунта. С кэшем в памяти процесса."""
    if not hasattr(fetch_vacancy_brief, "_cache"):
        fetch_vacancy_brief._cache = {}
    cache = fetch_vacancy_brief._cache
    if vid in cache:
        return cache[vid]

    token = _read_token_for_account()
    if not token:
        cache[vid] = ("", "")
        return cache[vid]

    req = urllib.request.Request(
        f"https://api.hh.ru/vacancies/{vid}",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "hh-applicant-tool/bot",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        name = (data.get("name") or "").strip()
        employer = ((data.get("employer") or {}).get("name") or "").strip()
        result = (name, employer)
    except Exception as e:
        print(f"fetch_vacancy_brief failed for {vid}: {e}", file=sys.stderr)
        result = ("", "")

    cache[vid] = result
    if len(cache) > 300:
        for k in list(cache.keys())[:100]:
            cache.pop(k, None)
    return result


def cmd_stats(today_only):
    items = parse_log_applies()
    if not items:
        return "📊 Откликов пока нет в логе."

    if today_only:
        today = now_msk().date()
        items = [x for x in items if x["ts"].date() == today]
        label = "сегодня"
    else:
        label = "всё время"

    total = len(items)
    lines = [f"<b>📊 Статистика ({label})</b>",
             f"Откликов: <b>{total}</b>"]

    if total > 0 and not today_only:
        first = min(x["ts"] for x in items)
        last = max(x["ts"] for x in items)
        lines.append(f"Первый: {fmt_dt(first)}")
        lines.append(f"Последний: {fmt_dt(last)}")

        # Разбивка по дням
        by_day = Counter(x["ts"].date().isoformat() for x in items)
        if len(by_day) <= 14:
            lines.append("\n<b>По дням:</b>")
            for day in sorted(by_day, reverse=True)[:14]:
                lines.append(f"• {day}: <b>{by_day[day]}</b>")
        else:
            lines.append("\n<b>Последние 7 дней:</b>")
            for day in sorted(by_day, reverse=True)[:7]:
                lines.append(f"• {day}: <b>{by_day[day]}</b>")

    return "\n".join(lines)


def cmd_recent():
    items = parse_log_applies()
    if not items:
        return "🕒 Откликов пока нет."

    recent = sorted(items, key=lambda x: x["ts"], reverse=True)[:10]

    lines = ["<b>🕒 Последние 10 откликов</b>"]
    for r in recent:
        vid = r["vid"]
        ts = fmt_dt(r["ts"])
        name, employer = fetch_vacancy_brief(vid)
        if name:
            title = html.escape(name)
            if employer:
                title += f" — {html.escape(employer)}"
        else:
            title = f"Вакансия {vid}"
        lines.append(
            f"\n• <b>{title}</b>\n"
            f"  🕒 {ts}\n"
            f"  🔗 https://hh.ru/vacancy/{vid}"
        )
    return "\n".join(lines)


POSITIVE_STATES = ("invitation", "phone_interview", "assessment", "offer")


# =================== Ручной отклик по ссылке ===================
# Отклик уходит тем же методом API, что и у утилиты, поэтому считается в тот же
# лимит 200/сутки. Чтобы статистика и счётчик лимита не разъехались, после
# успешного отклика в log.txt дописывается строка в формате утилиты — её
# подхватывают и watcher (уведомление), и парсер лимитов.
VACANCY_ID_RE = re.compile(r"(?:vacancy/|^|\s|/)(\d{6,})")
LETTER_VARIANTS_RE = re.compile(r"\{([^{}]*\|[^{}]*)\}")


def hh_post_negotiation(vacancy_id, resume_id, message):
    try:
        hh_api.request(active_hh_account(), '/negotiations', method='POST', fields={
            'vacancy_id': vacancy_id, 'resume_id': resume_id, 'message': message or ''})
        return True, ''
    except HHAPIError as exc:
        return False, str(exc)


def render_letter(template, vacancy, me):
    """Разворачивает {вариант1|вариант2} и подставляет плейсхолдеры."""
    text = LETTER_VARIANTS_RE.sub(lambda m: random.choice(m.group(1).split("|")), template)
    values = {
        "vacancy_name": (vacancy.get("name") or ""),
        "employer_name": ((vacancy.get("employer") or {}).get("name") or ""),
        "first_name": (me.get("first_name") or ""),
        "last_name": (me.get("last_name") or ""),
        "email": (me.get("email") or ""),
        "phone": "",
    }
    phones = me.get("phones") or []
    if phones:
        ph = phones[0]
        values["phone"] = ph.get("formatted") or f"{ph.get('country','')}{ph.get('city','')}{ph.get('number','')}"
    try:
        return text % values
    except (KeyError, ValueError):
        return text


def append_tool_log(vacancy_id, resume_id):
    """Синтетическая строка в лог утилиты — источник правды для статистики."""
    try:
        log_path = get_active_tool_log()
        stamp = now_msk().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
        line = (f"{stamp} - DEBUG - 201 POST https://api.hh.ru/negotiations "
                f"with params: {{'resume_id': '{resume_id}', "
                f"'vacancy_id': '{vacancy_id}', 'source': 'bot-manual'}}\n")
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        print(f"append_tool_log failed: {e}", file=sys.stderr)


def cmd_apply_links_prompt():
    pid, profile = profiles_lib.get_active_profile()
    if not profile:
        return "❌ Нет активного профиля."
    if not (profile.get("resume_id") or "").strip():
        return "❌ У профиля не выбрано резюме: «👤 Профиль» → «📄 Резюме»."
    state = compute_limits_state(parse_log_applies(), now_msk())
    PENDING["action"] = "apply_links"
    return (f"🎯 <b>Ручной отклик</b>\nПрофиль: <b>{html.escape(_active_profile_label())}</b>\n"
            f"Свободно слотов: <b>{state['free_now']}</b> из {HH_DAILY_LIMIT}\n\n"
            "Пришли ссылки на вакансии одним сообщением — по одной в строке или через пробел. "
            "Можно просто номера.\n"
            "Письмо возьмётся из профиля, плейсхолдеры подставятся как при обычной рассылке.\n\n"
            "/cancel — отмена.")


def cmd_apply_links_run(chat_id, text):
    return manual_flow.prepare(sys.modules[__name__], chat_id, text)


def cmd_reply_message_edit():
    _, profile = profiles_lib.get_active_profile()
    if not profile:
        return "❌ Нет активного профиля."
    current = (profile.get("reply_message") or "").strip()
    PENDING["action"] = "reply_message"
    body = f"<pre>{html.escape(current)}</pre>" if current else "<i>не задан</i>"
    return (f"💬 <b>Ответ работодателям</b>\nОдин и тот же текст уйдёт во все чаты, "
            "где ждут вашего ответа. Плейсхолдеры здесь не подставляются.\n\n"
            f"Сейчас:\n{body}\n\nПришли новый текст одним сообщением. "
            "«-» — очистить, /cancel — отмена.")


def cmd_reply_message_save(text):
    pid, profile = profiles_lib.get_active_profile()
    PENDING["action"] = None
    if not profile:
        return "❌ Нет активного профиля."
    text = (text or "").strip()
    if text == "-":
        text = ""
    if len(text) > 4000:
        return "⚠️ Слишком длинный текст (максимум 4000 символов)."
    profiles_lib.update_profile(pid, reply_message=text)
    return "✅ Шаблон ответа сохранён." if text else "✅ Шаблон ответа очищен."


TRACKED_FILE = CONFIG_DIR / "tracked.json"
TRACK_STATES = {
    "invitation": ("🎉", "Приглашение"),
    "phone_interview": ("📞", "Телефонное интервью"),
    "assessment": ("📝", "Тестовое задание"),
    "offer": ("🏆", "Оффер"),
    "discard": ("❌", "Отказ"),
    "response": ("⏳", "В ожидании"),
}


def load_tracked():
    try:
        with TRACKED_FILE.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_tracked(data):
    TRACKED_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TRACKED_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(TRACKED_FILE)


def cmd_track_vacancy(vacancy_id):
    """Кнопка «Отслеживать» под уведомлением об отклике."""
    pid, profile = profiles_lib.get_active_profile()
    if not profile:
        return "❌ Нет активного профиля."
    data = load_tracked()
    bucket = data.setdefault(pid, {})
    if vacancy_id in bucket:
        return "ℹ️ Вакансия уже в отслеживаемых."
    vacancy = hh_get(f"/vacancies/{vacancy_id}") or {}
    bucket[vacancy_id] = {
        "name": vacancy.get("name") or f"Вакансия {vacancy_id}",
        "employer": (vacancy.get("employer") or {}).get("name") or "",
        "added": now_msk().strftime("%Y-%m-%d %H:%M"),
    }
    save_tracked(data)
    return (f"👁 Отслеживаю: <b>{html.escape(bucket[vacancy_id]['name'])}</b>\n"
            "Статус — «📊 Просмотр» → «👁 Отслеживаемые».")


def tracked_with_states():
    """Отслеживаемые активного профиля со статусом отклика из API."""
    pid, _ = profiles_lib.get_active_profile()
    bucket = load_tracked().get(pid or "", {})
    if not bucket:
        return pid, []
    states = {}
    data = hh_get("/negotiations?per_page=100")
    if data:
        items = list(data.get("items", []) or [])
        for page in range(1, min(int(data.get("pages") or 1), 5)):
            more = hh_get(f"/negotiations?per_page=100&page={page}")
            items += (more or {}).get("items", []) or []
        for it in items:
            vid = str(((it.get("vacancy") or {}).get("id")) or "")
            if vid:
                states[vid] = (it.get("state") or {}).get("id") or ""
    rows = []
    for vid, info in bucket.items():
        rows.append((vid, info, states.get(vid, "")))
    order = {"offer": 0, "invitation": 1, "phone_interview": 2, "assessment": 3,
             "response": 4, "": 5, "discard": 6}
    rows.sort(key=lambda r: (order.get(r[2], 5), r[1].get("added", "")))
    return pid, rows


def build_menu_tracked():
    pid, rows = tracked_with_states()
    if not rows:
        return {"title": ("👁 <b>Отслеживаемые</b>\n\nПусто. Кнопка «👁 Отслеживать» "
                          "есть под каждым уведомлением об отклике."),
                "rows": [[("⬅️ Назад", "menu:view")]]}
    lines = [f"👁 <b>Отслеживаемые</b> ({len(rows)})"]
    for vid, info, state in rows:
        icon, label = TRACK_STATES.get(state, ("❔", "Нет отклика"))
        employer = f" @ {html.escape(info.get('employer') or '')}" if info.get("employer") else ""
        lines.append(f"{icon} <a href=\"https://hh.ru/vacancy/{vid}\">"
                     f"{html.escape(info.get('name') or vid)}</a>{employer} — {label}")
    buttons = [[(f"🗑 {(info.get('name') or vid)[:28]}", f"untrack:{vid}")]
               for vid, info, _ in rows[:10]]
    buttons.append([("🧹 Очистить всё", "untrack:all")])
    buttons.append([("⬅️ Назад", "menu:view")])
    return {"title": "\n".join(lines), "rows": buttons}


def cmd_invites():
    """Актуальные приглашения по активному аккаунту — из API, а не из лога."""
    data = hh_get("/negotiations?per_page=100")
    if not data:
        return "❌ Нет доступа к API. Проверь авторизацию аккаунта."
    items = list(data.get("items", []) or [])
    pages = min(int(data.get("pages") or 1), 5)
    for page in range(1, pages):
        more = hh_get(f"/negotiations?per_page=100&page={page}")
        items += (more or {}).get("items", []) or []

    buckets = {state: [] for state in POSITIVE_STATES}
    for it in items:
        state = (it.get("state") or {}).get("id")
        if state in buckets:
            buckets[state].append(it)

    total = sum(len(v) for v in buckets.values())
    if not total:
        return ("🎉 <b>Приглашения</b>\n\nПока пусто. "
                "Как только статус отклика сменится, уведомление придёт само.")

    lines = [f"🎉 <b>Актуальные приглашения</b> ({total})"]
    for state in POSITIVE_STATES:
        group = buckets[state]
        if not group:
            continue
        lines.append(f"\n<b>{html.escape(state_label(state))}</b> — {len(group)}")
        for it in sorted(group, key=lambda x: x.get("updated_at") or "", reverse=True)[:15]:
            vacancy = it.get("vacancy") or {}
            name = vacancy.get("name") or "—"
            employer = (vacancy.get("employer") or {}).get("name") or "—"
            url = vacancy.get("alternate_url") or ""
            updated = (it.get("updated_at") or "")[:10]
            title = f"<a href=\"{url}\">{html.escape(name)}</a>" if url else html.escape(name)
            mark = "🆕 " if it.get("has_updates") else ""
            lines.append(f"— {mark}{title} @ {html.escape(employer)} ({updated})")
    lines.append("\n💬 Переписка: hh.ru/applicant/negotiations")
    return "\n".join(lines)


def cmd_statuses():
    """Берёт текущие статусы откликов через HH API (не из локальной БД)."""
    data = hh_get("/negotiations?per_page=100")
    items = list(data.get("items", []) or [])
    pages = min(int(data.get("pages") or 1), 5)
    for page in range(1, pages):
        more = hh_get(f"/negotiations?per_page=100&page={page}")
        items += more.get("items", []) or []

    if not items:
        return "Откликов нет (или нет доступа к API)."

    counter = Counter()
    recent = []
    for it in items:
        state = (it.get("state") or {}).get("id") or (it.get("state") or {}).get("name") or "—"
        counter[state] += 1
        recent.append((
            (it.get("vacancy") or {}).get("name") or "—",
            ((it.get("vacancy") or {}).get("employer") or {}).get("name") or "—",
            state,
            (it.get("updated_at") or "")[:10],
        ))

    lines = [f"<b>📋 Статусы откликов на HH</b> (всего: {sum(counter.values())})"]
    for st, n in counter.most_common():
        lines.append(f"• {html.escape(state_label(st))}: <b>{n}</b>")

    lines.append("\n<b>Последние 10:</b>")
    for name, emp, st, upd in recent[:10]:
        lines.append(
            f"— <i>{html.escape(name)}</i> @ {html.escape(emp)} → "
            f"{html.escape(state_label(st))} ({upd})"
        )
    return "\n".join(lines)


# =================== Маршрутизация (кнопочный UI) ===================

def _active_profile_label():
    """Короткое имя активного профиля для заголовков."""
    pid, profile = profiles_lib.get_active_profile()
    if not profile:
        return "(нет профиля)"
    return profile.get("display_name") or pid


def cmd_profile_remove_active():
    """Удаляет активный профиль и его файл письма (после миграции файл
    у каждого профиля свой, поэтому удаление безопасно)."""
    items = profiles_lib.list_profiles()
    if len(items) <= 1:
        return "⚠️ Нельзя удалить последний профиль."
    pid, profile = profiles_lib.get_active_profile()
    if not profile:
        return "❌ Нет активного профиля."
    display = profile.get("display_name") or pid
    letter_name = profile.get("letter")
    try:
        profiles_lib.remove_profile(pid)
    except Exception as e:
        return f"⚠️ Ошибка: {html.escape(str(e))}"
    if letter_name:
        try:
            (profiles_lib.LETTERS_DIR / Path(letter_name).name).unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass
    return f"✅ Профиль «{html.escape(display)}» удалён вместе с письмом."


# =================== Инлайн-меню («🎛 Меню») ===================
# Меню: {"title": str, "rows": [[(label, callback_data), ...], ...]}.
# callback_data: "menu:<name>", "switch:<pid>", "del:yes", "cmd:<command>".

def build_menu_main():
    label = _active_profile_label()
    return {
        "title": f"<b>🎛 Меню</b>\nАктивный профиль: <b>{html.escape(label)}</b>",
        "rows": [
            [("📊 Просмотр", "menu:view"), ("🎛 Управление", "menu:ctl")],
            [("👤 Профиль", "menu:profile"), ("❓ Справка", "menu:help")],
        ],
    }


def build_menu_view():
    return {
        "title": "<b>📊 Просмотр</b>",
        "rows": [
            [("⚙️ Статус", "cmd:/status"), ("⏰ Лимит", "cmd:/next")],
            [("📅 Сегодня", "cmd:/stats_today"), ("📊 За всё", "cmd:/stats")],
            [("🕒 Последние", "cmd:/recent"), ("🎯 На HH", "cmd:/statuses")],
            [("🎉 Приглашения", "cmd:/invites")],
            [("👁 Отслеживаемые", "menu:tracked")],
            [("📜 Лог", "cmd:/log")],
            [("⬅️ Назад", "menu:main")],
        ],
    }


def build_menu_ctl():
    return {
        "title": "<b>🎛 Управление</b>",
        "rows": [
            [("▶️ Запустить", "cmd:/run"), ("⏸ Пауза", "cmd:/pause")],
            [("⏯ Возобновить", "cmd:/resume")],
            [("🎯 Откликнуться по ссылке", "cmd:/apply_links")],
            [("💬 Ответить работодателям", "cmd:/reply")],
            [("🧹 Удалить отказы", "cmd:/cleanup")],
            [("♻️ Сбросить пропущенные", "cmd:/clear_skipped")],
            [("💾 Бэкап в чат", "cmd:/backup")],
            [("⬅️ Назад", "menu:main")],
        ],
    }


def build_menu_profile():
    label = _active_profile_label()
    return {
        "title": f"<b>👤 Профиль: {html.escape(label)}</b>",
        "rows": [
            [("📝 Письмо", "cmd:/letter"), ("✏️ Изменить", "cmd:/letter_set")],
            [("🔍 Запрос", "cmd:/search"), ("✏️ Изменить", "cmd:/search_set")],
            [("🌍 Регион", "cmd:/area"), ("📄 Резюме", "menu:resume")],
            [("💬 Ответ работодателям", "cmd:/reply_msg")],
            [("🔎 Фильтры поиска", "menu:filters")],
            [("🤖 Настройки AI", "menu:ai")],
            [("🏷 Переименовать", "cmd:/profile_rename")],
            [("🔑 Авторизовать HH", "cmd:/auth")],
            [("⟳ Сменить", "menu:switch"), ("➕ Создать", "cmd:/profile_add")],
            [("📋 Список", "cmd:/profiles"), ("🗑 Удалить", "menu:del")],
            [("⬅️ Назад", "menu:main")],
        ],
    }


AI_FILTER_TITLES = {
    "": "выключен",
    "heavy": "heavy",
    "light": "light",
    "custom": "custom",
}


def _ai_flag(value):
    return "вкл" if value else "выкл"


def _ai_preview(text, limit=40):
    """Короткая выжимка промпта для подписи кнопки."""
    text = (text or "").strip().replace("\n", " ")
    if not text:
        return "по умолчанию"
    return text[:limit] + "…" if len(text) > limit else text


def build_menu_ai():
    pid, profile = profiles_lib.get_active_profile()
    if not profile:
        return {"title": "❌ Нет активного профиля.", "rows": [[("⬅️ Назад", "menu:profile")]]}
    ai = profiles_lib.get_ai(profile)
    label = _active_profile_label()
    warn = ""
    if ai["filter"] == "custom" and not ai["filter_prompt"].strip():
        warn = "\n⚠️ Режим <b>custom</b> без промпта — рассылка не запустится."
    title = (
        f"🤖 <b>AI профиля «{html.escape(label)}»</b>\n"
        f"Письма: <b>{_ai_flag(ai['enabled'])}</b>, "
        f"фильтр: <b>{AI_FILTER_TITLES.get(ai['filter'], ai['filter'])}</b>, "
        f"тест-прогон: <b>{_ai_flag(ai['dry_run'])}</b>{warn}"
    )
    return {
        "title": title,
        "rows": [
            [(f"⚡ Письма: {_ai_flag(ai['enabled'])}", "ai:toggle")],
            [(f"🔎 OCR CAPTCHA: {_ai_flag(ai['captcha_ocr'])}", "ai:ocr")],
            [(f"🎯 Фильтр: {AI_FILTER_TITLES.get(ai['filter'], ai['filter'])}", "menu:ai_filter")],
            [("✏️ Промпт фильтра", "cmd:/ai_filter_prompt")],
            [("✏️ Промпт письма", "cmd:/ai_message_prompt")],
            [("🧠 Роль модели", "cmd:/ai_system_prompt")],
            [(f"⏱ Лимит: {ai['rate_limit']}/мин", "cmd:/ai_rate")],
            [(f"🧪 Тест-прогон: {_ai_flag(ai['dry_run'])}", "ai:dry")],
            [("🔐 Доступ к AI", "menu:ai_access")],
            [("⬅️ Назад", "menu:profile")],
        ],
    }


def build_menu_ai_filter():
    _, profile = profiles_lib.get_active_profile()
    current = profiles_lib.get_ai(profile)["filter"] if profile else ""
    options = [
        ("", "🚫 Выключен"),
        ("light", "🪶 light — название и навыки"),
        ("heavy", "🧱 heavy — вакансия и резюме"),
        ("custom", "🎛 custom — свой промпт"),
    ]
    rows = [[(("✅ " if mode == current else "") + text, f"aifilter:{mode}")]
            for mode, text in options]
    rows.append([("⬅️ Назад", "menu:ai")])
    return {
        "title": ("🎯 <b>AI-фильтр вакансий</b>\n"
                  "Модель оценивает вакансию до отклика. <b>light</b> дешевле и быстрее, "
                  "<b>heavy</b> точнее и дороже, <b>custom</b> работает по вашему промпту."),
        "rows": rows,
    }


AI_PROMPT_FIELDS = {
    "/ai_filter_prompt": ("filter_prompt", "промпт AI-фильтра",
                          "Критерии, по которым модель отсеивает вакансии. "
                          "Работает только в режиме <b>custom</b>."),
    "/ai_message_prompt": ("message_prompt", "промпт сопроводительного письма",
                           "Что именно генерировать. Пусто — дефолт утилиты "
                           "(письмо на 5–7 предложений)."),
    "/ai_system_prompt": ("system_prompt", "роль модели",
                          "Системный промпт для генерации писем. Пусто — дефолт утилиты."),
}


def cmd_ai_prompt_edit(command):
    pid, profile = profiles_lib.get_active_profile()
    if not profile:
        return "❌ Нет активного профиля."
    field, title, hint = AI_PROMPT_FIELDS[command]
    current = profiles_lib.get_ai(profile)[field]
    PENDING["action"] = f"ai_set:{field}"
    body = f"<pre>{html.escape(current)}</pre>" if current.strip() else "<i>не задан</i>"
    return (f"✏️ <b>Правка: {title}</b>\n{hint}\n\nСейчас:\n{body}\n\n"
            "Пришли новый текст <b>одним сообщением</b>.\n"
            "«-» — сбросить на значение по умолчанию, /cancel — отмена.")


def cmd_ai_rate_edit():
    _, profile = profiles_lib.get_active_profile()
    if not profile:
        return "❌ Нет активного профиля."
    PENDING["action"] = "ai_set:rate_limit"
    return (f"⏱ <b>Лимит запросов к AI</b>\nСейчас: <b>{profiles_lib.get_ai(profile)['rate_limit']}</b> в минуту.\n\n"
            "Пришли число (1–600). Дефолт утилиты — 40. /cancel — отмена.")


def cmd_ai_save(field, text):
    pid, profile = profiles_lib.get_active_profile()
    if not profile:
        PENDING["action"] = None
        return "❌ Нет активного профиля."
    text = (text or "").strip()
    try:
        if field == "rate_limit":
            try:
                value = int(text)
            except ValueError:
                return "⚠️ Нужно целое число. Ещё раз или /cancel."
            if not 1 <= value <= 600:
                return "⚠️ Допустимый диапазон — от 1 до 600."
            profiles_lib.update_ai(pid, rate_limit=value)
            PENDING["action"] = None
            return f"✅ Лимит запросов к AI: <b>{value}</b> в минуту."
        if text == "-":
            text = ""
        if len(text) > 4000:
            return "⚠️ Слишком длинный промпт (максимум 4000 символов)."
        profiles_lib.update_ai(pid, **{field: text})
        PENDING["action"] = None
        title = next(t for _, (f, t, _) in AI_PROMPT_FIELDS.items() if f == field)
        return f"✅ Обновлено: {title}." if text else f"✅ Сброшено на умолчание: {title}."
    except Exception as e:
        PENDING["action"] = None
        return f"⚠️ Ошибка сохранения: {html.escape(str(e))}"


# =================== AI-конфигурация HH-аккаунта ===================
# Ключ, endpoint и модель лежат не в profiles.json, а в config.json аккаунта,
# отдельными секциями под каждую задачу. Читаем файл напрямую, пишем через CLI,
# чтобы не спорить с утилитой за формат конфига.
AI_SECTION_COVER = "openai.cover_letter"
AI_SECTION_FILTER = "openai.vacancy_filter"
AI_SECTION_CAPTCHA = "openai.captcha"
AI_SECTIONS = (AI_SECTION_COVER, AI_SECTION_FILTER, AI_SECTION_CAPTCHA)

AI_ENDPOINTS = [
    ("OpenAI", "https://api.openai.com/v1"),
    ("OpenRouter", "https://openrouter.ai/api/v1"),
    ("Ollama", "http://host.docker.internal:11434/v1"),
]


def _mask_secret(value):
    value = (value or "").strip()
    if not value:
        return ""
    return value[:6] + "…" + value[-4:] if len(value) > 14 else "…" * 3


def read_account_config(hh_account):
    try:
        return read_profile_config(CONFIG_DIR, hh_account)
    except (OSError, ValueError):
        return {}


def read_ai_config(hh_account):
    """Сводка AI-настроек аккаунта: что задано в секции писем + прокси."""
    cfg = read_account_config(hh_account)
    cover = (cfg.get("openai") or {}).get("cover_letter") or (cfg.get("openai") or {}).get("default") or {}
    captcha = (cfg.get("openai") or {}).get("captcha") or {}
    return {
        "api_key": cover.get("api_key") or "",
        "base_url": cover.get("base_url") or "",
        "model": cover.get("model") or "",
        "proxy_url": (cfg.get("openai") or {}).get("default", {}).get("proxy_url") or "",
        "captcha": bool(captcha.get("api_key")),
    }


def hh_config_set(hh_account, key, value):
    """`hh-applicant-tool --profile X config --set key value`."""
    try:
        res = subprocess.run(cli_args(hh_account, 'config', key, value),
                             capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return False, "hh-applicant-tool не найден в PATH"
    except subprocess.TimeoutExpired:
        return False, "команда не завершилась за 60 секунд"
    except Exception as e:
        return False, str(e)
    if res.returncode != 0:
        return False, ((res.stderr or res.stdout or "").strip() or f"код {res.returncode}")
    return True, ""


def hh_config_unset(hh_account, key):
    try:
        res = subprocess.run(cli_args(hh_account, 'config', '--delete', key),
                             capture_output=True, text=True, timeout=60)
        return res.returncode == 0, "" if res.returncode == 0 else "Не удалось удалить настройку"
    except Exception as e:
        return False, str(e)


def ai_config_apply(hh_account, field, value, sections=None):
    """Пишет одно поле сразу во все нужные секции. Возвращает список ошибок."""
    errors = []
    for section in (sections or ("openai.default", AI_SECTION_COVER, AI_SECTION_FILTER)):
        ok, err = hh_config_set(hh_account, f"{section}.{field}", value)
        if not ok:
            errors.append(f"{section}: {err}")
    return errors


def build_menu_ai_access():
    _, profile = profiles_lib.get_active_profile()
    if not profile:
        return {"title": "❌ Нет активного профиля.", "rows": [[("⬅️ Назад", "menu:ai")]]}
    account = profile.get("hh_account", "main")
    cfg = read_ai_config(account)
    endpoint = next((name for name, url in AI_ENDPOINTS if url == cfg["base_url"]),
                    cfg["base_url"] or "не задан")
    warn = "" if cfg["api_key"] and cfg["base_url"] else (
        "\n⚠️ Без ключа и endpoint рассылка с AI упадёт с «API-ключ не задан»."
    )
    return {
        "title": (f"🔐 <b>Доступ к AI</b>\nАккаунт HH: <b>{html.escape(account)}</b> — "
                  "настройки общие для всех его профилей.\n"
                  f"Ключ: <b>{_mask_secret(cfg['api_key']) or 'не задан'}</b>{warn}"),
        "rows": [
            [(f"🔑 Ключ: {'задан' if cfg['api_key'] else 'нет'}", "cmd:/ai_key")],
            [(f"🌐 Endpoint: {endpoint}", "menu:ai_endpoint")],
            [(f"🧠 Модель: {cfg['model'] or 'не задана'}", "cmd:/ai_model")],
            [(f"🕸 Прокси: {'задан' if cfg['proxy_url'] else 'нет'}", "cmd:/ai_proxy")],
            [(f"🖼 Капча через AI: {'вкл' if cfg['captcha'] else 'выкл'}", "aicfg:captcha")],
            [("⬅️ Назад", "menu:ai")],
        ],
    }


def build_menu_ai_endpoint():
    _, profile = profiles_lib.get_active_profile()
    current = read_ai_config((profile or {}).get("hh_account", "main"))["base_url"]
    rows = [[(("✅ " if url == current else "") + name, f"aiurl:{idx}")]
            for idx, (name, url) in enumerate(AI_ENDPOINTS)]
    rows.append([("✏️ Указать вручную", "cmd:/ai_base_url")])
    rows.append([("⬅️ Назад", "menu:ai_access")])
    return {
        "title": ("🌐 <b>Endpoint</b>\nУтилита требует полный URL метода chat/completions, "
                  "а не адрес сервера. Совместимые провайдеры подставляются кнопками ниже."),
        "rows": rows,
    }


AI_CONFIG_FIELDS = {
    "/ai_key": ("api_key", "API-ключ",
                "Ключ пишется в секции писем, фильтра и — если включена — капчи.\n"
                "Сообщение с ключом бот удалит из чата сразу после сохранения."),
    "/ai_model": ("model", "модель",
                  "Например <code>gpt-4o</code> или <code>gpt-4o-mini</code>. "
                  "Пусто — утилита возьмёт свою по умолчанию и напишет предупреждение."),
    "/ai_base_url": ("base_url", "endpoint",
                     "Полный URL, например <code>https://api.openai.com/v1</code>."),
    "/ai_proxy": ("proxy_url", "прокси для OpenAI",
                  "Отдельный прокси только для запросов к модели, "
                  "например <code>socks5://user:pass@host:1080</code>. «-» — убрать."),
}


def cmd_ai_config_edit(command):
    _, profile = profiles_lib.get_active_profile()
    if not profile:
        return "❌ Нет активного профиля."
    field, title, hint = AI_CONFIG_FIELDS[command]
    account = profile.get("hh_account", "main")
    current = read_ai_config(account).get(field, "")
    shown = _mask_secret(current) if field == "api_key" else (redact(current) or "")
    PENDING["action"] = f"aicfg:{field}"
    return (f"✏️ <b>{title}</b> для аккаунта <b>{html.escape(account)}</b>\n{hint}\n\n"
            f"Сейчас: <code>{html.escape(shown) if shown else 'не задано'}</code>\n\n"
            "Пришли значение одним сообщением. /cancel — отмена.")


def cmd_ai_config_save(field, text):
    _, profile = profiles_lib.get_active_profile()
    if not profile:
        PENDING["action"] = None
        return "❌ Нет активного профиля."
    account = profile.get("hh_account", "main")
    value = (text or "").strip()
    PENDING["action"] = None

    if field == "proxy_url":
        if value == "-":
            hh_config_unset(account, "openai.default.proxy_url")
            return "✅ Прокси для OpenAI убран."
        ok, err = hh_config_set(account, "openai.default.proxy_url", value)
        return "✅ Прокси сохранён." if ok else f"⚠️ Не сохранилось: {html.escape(err)}"

    if field == "base_url" and not value.startswith(("http://", "https://")):
        return "⚠️ Нужен полный URL со схемой http:// или https://. Ещё раз или /cancel."
    if field == "api_key" and len(value) < 8:
        return "⚠️ Ключ выглядит слишком коротким. Ещё раз или /cancel."

    sections = ["openai.default", AI_SECTION_COVER, AI_SECTION_FILTER]
    if read_ai_config(account)["captcha"]:
        sections.append(AI_SECTION_CAPTCHA)
    errors = ai_config_apply(account, field, value, sections)
    title = AI_CONFIG_FIELDS[next(k for k, v in AI_CONFIG_FIELDS.items() if v[0] == field)][1]
    if errors:
        return "⚠️ Не сохранилось:\n<pre>" + html.escape("\n".join(errors)) + "</pre>"
    return f"✅ Сохранено: {title} (секций: {len(sections)})."


# =================== Фильтры поиска ===================
FILTER_LABELS = {
    "experience": {"": "любой", "noExperience": "без опыта", "between1And3": "1–3 года",
                   "between3And6": "3–6 лет", "moreThan6": "более 6 лет"},
    "schedule": {"": "любой", "fullDay": "полный день", "shift": "сменный",
                 "flexible": "гибкий", "remote": "удалённый", "flyInFlyOut": "вахта"},
    "work_format": {"REMOTE": "удалённо", "HYBRID": "гибрид",
                    "ON_SITE": "в офисе", "FIELD_WORK": "разъездная"},
    "order_by": {"": "по умолчанию", "publication_time": "по дате", "salary_desc": "зарплата ↓",
                 "salary_asc": "зарплата ↑", "relevance": "по релевантности"},
}


def build_menu_filters():
    pid, profile = profiles_lib.get_active_profile()
    if not profile:
        return {"title": "❌ Нет активного профиля.", "rows": [[("⬅️ Назад", "menu:profile")]]}
    f = profiles_lib.get_filters(profile)
    formats = ", ".join(FILTER_LABELS["work_format"][v] for v in f["work_format"]) or "любой"
    banned = len(f["excluded_employer_ids"])
    return {
        "title": (f"🔎 <b>Фильтры поиска «{html.escape(_active_profile_label())}»</b>\n"
                  "Отсекают вакансии на стороне HH — до отклика и до вызова AI, "
                  "то есть бесплатно."),
        "rows": [
            [(f"💼 Опыт: {FILTER_LABELS['experience'][f['experience']]}", "menu:f_exp")],
            [(f"🕘 График: {FILTER_LABELS['schedule'][f['schedule']]}", "menu:f_sched")],
            [(f"🏠 Формат: {formats}", "menu:f_format")],
            [(f"💰 Зарплата от: {f['salary'] or 'любой'}", "cmd:/f_salary")],
            [(f"💵 Только с зарплатой: {_ai_flag(f['only_with_salary'])}", "flt:only_with_salary")],
            [(f"📅 Период: {str(f['period']) + ' дн' if f['period'] else 'любой'}", "cmd:/f_period")],
            [(f"↕️ Сортировка: {FILTER_LABELS['order_by'][f['order_by']]}", "menu:f_order")],
            [(f"🧪 Пропускать тесты: {_ai_flag(f['skip_tests'])}", "flt:skip_tests")],
            [(f"📧 Письмо на email: {_ai_flag(f['send_email'])}", "flt:send_email")],
            [(f"🚫 Чёрный список: {banned}", "menu:f_banned")],
            [("⬅️ Назад", "menu:profile")],
        ],
    }


def _build_filter_picker(field, title, hint):
    _, profile = profiles_lib.get_active_profile()
    current = profiles_lib.get_filters(profile)[field] if profile else ""
    rows = [[(("✅ " if value == current else "") + label, f"fset:{field}:{value}")]
            for value, label in FILTER_LABELS[field].items()]
    rows.append([("⬅️ Назад", "menu:filters")])
    return {"title": f"<b>{title}</b>\n{hint}", "rows": rows}


def build_menu_f_exp():
    return _build_filter_picker("experience", "💼 Опыт работы",
                                "Соответствует фильтру опыта в поиске HH.")


def build_menu_f_sched():
    return _build_filter_picker("schedule", "🕘 График работы",
                                "Старый фильтр HH; для удалёнки надёжнее «Формат работы».")


def build_menu_f_order():
    return _build_filter_picker("order_by", "↕️ Сортировка выдачи",
                                "«По дате» полезна: свежий отклик рекрутер видит первым.")


def build_menu_f_format():
    _, profile = profiles_lib.get_active_profile()
    current = profiles_lib.get_filters(profile)["work_format"] if profile else []
    rows = [[(("✅ " if value in current else "") + label, f"fmt:{value}")]
            for value, label in FILTER_LABELS["work_format"].items()]
    rows.append([("⬅️ Назад", "menu:filters")])
    return {"title": ("🏠 <b>Формат работы</b>\nМожно отметить несколько. "
                      "Пусто — фильтр не передаётся."), "rows": rows}


def build_menu_f_banned():
    _, profile = profiles_lib.get_active_profile()
    ids = profiles_lib.get_filters(profile)["excluded_employer_ids"] if profile else []
    rows = [[(f"🗑 {eid}", f"unban:{eid}")] for eid in ids[:20]]
    if ids:
        rows.append([("🧹 Очистить список", "unban:all")])
    rows.append([("⬅️ Назад", "menu:filters")])
    return {
        "title": ("🚫 <b>Чёрный список работодателей</b>\n"
                  + (f"В списке: {len(ids)}. Их вакансии исключаются из выдачи."
                     if ids else "Пуст. Работодатели добавляются кнопкой под уведомлением об отклике.")),
        "rows": rows,
    }


FILTER_NUMERIC = {
    "/f_max": ("max_responses", "Лимит откликов", "Максимум за один запуск (не менее 1)."),
    "/f_salary": ("salary", "💰 Зарплата от",
                  "Минимальная зарплата в рублях. 0 — не фильтровать."),
    "/f_period": ("period", "📅 Период публикации",
                  "Искать вакансии за последние N дней. 0 — без ограничения."),
}


def cmd_filter_number_edit(command):
    _, profile = profiles_lib.get_active_profile()
    if not profile:
        return "❌ Нет активного профиля."
    field, title, hint = FILTER_NUMERIC[command]
    PENDING["action"] = f"flt_num:{field}"
    return (f"✏️ <b>{title}</b>\n{hint}\n\n"
            f"Сейчас: <b>{profiles_lib.get_filters(profile)[field]}</b>\n\n"
            "Пришли число. /cancel — отмена.")


def cmd_filter_number_save(field, text):
    pid, profile = profiles_lib.get_active_profile()
    if not profile:
        PENDING["action"] = None
        return "❌ Нет активного профиля."
    try:
        value = int((text or "").strip())
    except ValueError:
        return "⚠️ Нужно целое число. Ещё раз или /cancel."
    if value < 0 or value > 10_000_000:
        return "⚠️ Значение вне разумного диапазона."
    profiles_lib.update_filters(pid, **{field: value})
    PENDING["action"] = None
    title = FILTER_NUMERIC[next(k for k, v in FILTER_NUMERIC.items() if v[0] == field)][1]
    return f"✅ {title}: <b>{value or 'не задано'}</b>"


def cmd_clear_skipped(chat_id):
    """`clear-skipped`: сброс списка пропущенных вакансий."""
    _, profile = profiles_lib.get_active_profile()
    if not profile:
        return "❌ Нет активного профиля."
    account = profile.get("hh_account", "main")

    def runner():
        try:
            res = subprocess.run(
                cli_args(account, "clear-negotiations"),
                capture_output=True, text=True, timeout=300,
            )
            out = ((res.stdout or "") + (res.stderr or ""))[-1000:]
            head = "✅ Список пропущенных очищен." if res.returncode == 0 else f"⚠️ Код {res.returncode}"
            send(chat_id, f"{head}\n\n<pre>{html.escape(out or 'нет вывода')}</pre>")
        except FileNotFoundError:
            send(chat_id, "⚠️ hh-applicant-tool не найден в PATH.")
        except Exception as e:
            send(chat_id, f"⚠️ Ошибка: {html.escape(str(e))}")

    mini_app_context.make_thread(target=runner, daemon=True).start()
    return "🚀 Чищу список пропущенных вакансий."


def cmd_backup(chat_id):
    """Архив config/, profiles.json и letters/ отдельным файлом в чат."""
    def runner():
        try:
            res = subprocess.run([sys.executable, str(profiles_lib.APP_DIR / "backup.py")],
                                 capture_output=True, text=True, timeout=600)
            if res.returncode != 0:
                out = ((res.stderr or "") + (res.stdout or ""))[-800:]
                send(chat_id, f"⚠️ Бэкап не собрался:\n<pre>{html.escape(redact(out))}</pre>")
        except Exception as e:
            send(chat_id, f"⚠️ Ошибка бэкапа: {html.escape(str(e))}")

    mini_app_context.make_thread(target=runner, daemon=True).start()
    return "💾 Собираю бэкап, файл придёт следующим сообщением."


def cmd_ban_employer(chat_id, vacancy_id):
    """Добавляет работодателя вакансии в чёрный список активного профиля."""
    pid, profile = profiles_lib.get_active_profile()
    if not profile:
        return "❌ Нет активного профиля."
    data = hh_get(f"/vacancies/{vacancy_id}")
    employer = (data or {}).get("employer") or {}
    eid = str(employer.get("id") or "")
    name = employer.get("name") or f"ID {eid}"
    if not eid.isdigit():
        return "⚠️ Не удалось определить работодателя через API."
    try:
        added = profiles_lib.ban_employer(pid, eid)
    except Exception as e:
        return f"⚠️ Ошибка: {html.escape(str(e))}"
    if not added:
        return f"ℹ️ {html.escape(name)} уже в чёрном списке."
    return (f"🚫 <b>{html.escape(name)}</b> добавлен в чёрный список профиля "
            f"«{html.escape(_active_profile_label())}». "
            "Его вакансии больше не попадут в выдачу.")


def build_menu_switch():
    active_pid, _ = profiles_lib.get_active_profile()
    rows = []
    for pid, p in profiles_lib.list_profiles():
        display = p.get("display_name") or pid
        marker = "▶️ " if pid == active_pid else ""
        rows.append([(f"{marker}{display}", f"switch:{pid}")])
    rows.append([("⬅️ Назад", "menu:profile")])
    return {"title": "<b>⟳ Сменить профиль</b>\nВыбери активный профиль:", "rows": rows}


def build_menu_del():
    if len(profiles_lib.list_profiles()) <= 1:
        return {
            "title": "🗑 <b>Удаление профиля</b>\n⚠️ Нельзя удалить последний профиль.",
            "rows": [[("⬅️ Назад", "menu:profile")]],
        }
    label = _active_profile_label()
    return {
        "title": (
            f"🗑 <b>Удалить активный профиль «{html.escape(label)}»?</b>\n"
            "Профиль и его файл письма будут удалены. Действие необратимо."
        ),
        "rows": [[("✅ Да, удалить", "del:yes"), ("❌ Нет", "menu:profile")]],
    }


def _show_resume_change(chat_id, message_id):
    """Инлайн-выбор резюме для активного профиля (правит сообщение на месте)."""
    pid, profile = profiles_lib.get_active_profile()
    if not profile:
        send_menu(chat_id, {"title": "❌ Нет активного профиля.",
                            "rows": [[("⬅️ Назад", "menu:profile")]]}, edit_message_id=message_id)
        return
    hh_account = profile.get("hh_account", "")
    if not token_path(CONFIG_DIR, hh_account):
        send_menu(chat_id, {
            "title": (f"📄 <b>Резюме</b>\nАккаунт <code>{html.escape(hh_account)}</code> не авторизован — "
                      "список резюме недоступен. Сначала «🔑 Авторизовать HH»."),
            "rows": [[("⬅️ Назад", "menu:profile")]],
        }, edit_message_id=message_id)
        return
    resumes = fetch_resumes_for_account(hh_account)
    if not resumes:
        send_menu(chat_id, {"title": "📄 <b>Резюме</b>\nУ аккаунта нет резюме на hh (или не удалось получить список).",
                            "rows": [[("⬅️ Назад", "menu:profile")]]}, edit_message_id=message_id)
        return
    RESUME_PICK["pid"] = pid
    RESUME_PICK["resumes"] = resumes
    current = profile.get("resume_id") or ""
    rows = []
    for idx, (rid, title) in enumerate(resumes):
        mark = "✅ " if rid == current else ""
        label = title if len(title) <= 38 else title[:37] + "…"
        rows.append([(f"{mark}{label}", f"setres:{idx}")])
    rows.append([(("✅ " if not current else "") + "↺ По умолчанию", "setres:none")])
    rows.append([("⬅️ Назад", "menu:profile")])
    send_menu(chat_id, {
        "title": f"📄 <b>Резюме профиля «{html.escape(profile.get('display_name') or pid)}»</b>\nТекущее — ✅:",
        "rows": rows,
    }, edit_message_id=message_id)


def build_menu_help():
    return {"title": cmd_help(), "rows": [[("⬅️ Назад", "menu:main")]]}


def menu_by_name(name):
    builders = {
        "main": build_menu_main,
        "view": build_menu_view,
        "ctl": build_menu_ctl,
        "profile": build_menu_profile,
        "ai": build_menu_ai,
        "ai_filter": build_menu_ai_filter,
        "filters": build_menu_filters,
        "f_exp": build_menu_f_exp,
        "f_sched": build_menu_f_sched,
        "f_order": build_menu_f_order,
        "f_format": build_menu_f_format,
        "f_banned": build_menu_f_banned,
        "tracked": build_menu_tracked,
        "ai_access": build_menu_ai_access,
        "ai_endpoint": build_menu_ai_endpoint,
        "switch": build_menu_switch,
        "del": build_menu_del,
        "help": build_menu_help,
    }
    builder = builders.get(name)
    return builder() if builder else None


# Ширина инлайн-кнопок в ряду делится поровну, поэтому длинная подпись рядом
# с короткой обрезается многоточием. Ряды с широкими подписями раскладываем
# по одной кнопке на строку — правило действует и на динамические меню.
MAX_PAIR_WIDTH = 15


def _label_width(label):
    """Примерная ширина подписи: эмодзи занимают две позиции, модификаторы — ноль."""
    width = 0
    for ch in label:
        code = ord(ch)
        if code in (0xFE0F, 0x200D) or 0x1F3FB <= code <= 0x1F3FF:
            continue
        width += 2 if (0x2190 <= code <= 0x2BFF or code >= 0x1F000) else 1
    return width


def _fit_rows(rows):
    result = []
    for row in rows:
        if len(row) > 1 and any(_label_width(label) > MAX_PAIR_WIDTH for label, _ in row):
            result.extend([[button] for button in row])
        else:
            result.append(row)
    return result


_CURRENT_MENU = {}
_BAN_CONFIRM = {}


def refresh_ban_confirmation(chat_id, vacancy_id):
    """Recover a legacy notification action without trusting an expired menu."""
    import secrets
    pid,profile=profiles_lib.get_active_profile()
    if not profile:
        send(chat_id,'Нет активного профиля.'); return
    now=time.monotonic()
    for key,item in list(_BAN_CONFIRM.items()):
        if item[3]<now:
            _BAN_CONFIRM.pop(key,None)
    token=secrets.token_hex(8)
    _BAN_CONFIRM[token]=(str(chat_id),pid,vacancy_id,now+300)
    label=html.escape(profile.get('display_name') or pid)
    api_call('sendMessage',chat_id=chat_id,
        text=f'Добавить работодателя вакансии {vacancy_id} в чёрный список профиля «{label}»?',
        parse_mode='HTML',reply_markup=json.dumps({'inline_keyboard':[[
            {'text':'Добавить','callback_data':'banconfirm:'+token},
            {'text':'Отмена','callback_data':'bancancel:'+token}]]}))

def open_mini_app(chat_id, text='Панель управления доступна в приложении.'):
    return api_call('sendMessage', chat_id=chat_id, text=text, parse_mode='HTML',
                    reply_markup=json.dumps({'inline_keyboard': [[{
                        'text':'Открыть панель', 'web_app':{'url':MINI_APP_URL}}]]}))

def configure_mini_app_menu():
    if not MINI_APP_URL: return
    api_call('setChatMenuButton', chat_id=TG_CHAT, menu_button=json.dumps({
        'type':'web_app', 'text':'Панель', 'web_app':{'url':MINI_APP_URL}}))
    api_call('sendMessage', chat_id=TG_CHAT, text='Управление перенесено в приложение. Сводки и уведомления остаются здесь.',
             reply_markup=json.dumps({'remove_keyboard':True}))

def send_menu(chat_id, menu, edit_message_id=None, force_classic=False):
    """Шлёт (или редактирует на месте) сообщение с инлайн-меню."""
    if menu and mini_app_context.menu(menu, profiles_lib.get_active_profile()[0]): return
    if menu and MINI_APP_URL and not force_classic and not VK:
        return open_mini_app(chat_id)
    if not menu:
        return
    if VK:
        return VK.send_menu(chat_id, menu["title"], _fit_rows(menu["rows"]),
                            edit_message_id=edit_message_id)
    import secrets
    token = secrets.token_hex(4)
    _CURRENT_MENU[str(chat_id)] = (token, profiles_lib.get_active_profile()[0], time.monotonic() + 1800)
    def interactive_button(label, data):
        payload = f'm:{token}:{data}'
        if len(payload.encode('utf-8')) > 64: raise ValueError('Callback payload too long')
        button = {"text": label, "callback_data": payload}
        # Telegram Bot API supports optional colored inline buttons. Opt-in:
        # TG_INTERACTIVE_BUTTONS=1; omit style on older API gateways.
        if os.environ.get("TG_INTERACTIVE_BUTTONS") == "1":
            if data in ("del:yes", "cmd:/pause"):
                button["style"] = "danger"
            elif data in ("cmd:/start", "cmd:/resume") or data.endswith(":yes"):
                button["style"] = "success"
            elif data.startswith("menu:"):
                button["style"] = "primary"
        return button

    keyboard = {
        "inline_keyboard": [
            [interactive_button(label, data) for label, data in row]
            for row in _fit_rows(menu["rows"])
        ]
    }
    if not force_classic and str(chat_id) not in _CLASSIC_CHATS:
        if HTML_BUBBLES.try_menu(chat_id, menu["title"], keyboard,
                                 profiles_lib.get_active_profile()[0], edit_message_id):
            return
    # Immutable widget HTML cannot become text through editMessageText.
    if HTML_BUBBLES.owns(chat_id, edit_message_id):
        edit_message_id = None
    params = {
        "chat_id": chat_id,
        "text": menu["title"],
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
        "reply_markup": json.dumps(keyboard),
    }
    if edit_message_id:
        params["message_id"] = edit_message_id
        method = "editMessageText"
    else:
        method = "sendMessage"
    try:
        api_call(method, **params)
    except Exception as e:
        print(f"send_menu failed: {e}", file=sys.stderr)


def run_command(chat_id, cmd):
    """Команды из инлайн-кнопок (callback 'cmd:<command>'). Возвращает текст."""
    handlers = {
        "/schedule_set": schedule_prompt,
        "/history": history_message,
        "/status": cmd_status,
        "/next": cmd_next,
        "/stats_today": lambda: cmd_stats(True),
        "/stats": lambda: cmd_stats(False),
        "/recent": cmd_recent,
        "/statuses": cmd_statuses,
        "/invites": cmd_invites,
        "/apply_links": cmd_apply_links_prompt,
        "/reply_msg": cmd_reply_message_edit,
        "/log": cmd_log,
        "/run": lambda: cmd_run(chat_id),
        "/pause": cmd_pause,
        "/resume": cmd_resume,
        "/cleanup": lambda: cmd_cleanup(chat_id),
        "/reply": lambda: cmd_reply_employers(chat_id),
        "/clear_skipped": lambda: cmd_clear_skipped(chat_id),
        "/backup": lambda: cmd_backup(chat_id),
        "/f_max": lambda: cmd_filter_number_edit("/f_max"),
        "/f_salary": lambda: cmd_filter_number_edit("/f_salary"),
        "/f_period": lambda: cmd_filter_number_edit("/f_period"),
        "/ai_filter_prompt": lambda: cmd_ai_prompt_edit("/ai_filter_prompt"),
        "/ai_message_prompt": lambda: cmd_ai_prompt_edit("/ai_message_prompt"),
        "/ai_system_prompt": lambda: cmd_ai_prompt_edit("/ai_system_prompt"),
        "/ai_rate": cmd_ai_rate_edit,
        "/ai_key": lambda: cmd_ai_config_edit("/ai_key"),
        "/ai_model": lambda: cmd_ai_config_edit("/ai_model"),
        "/ai_base_url": lambda: cmd_ai_config_edit("/ai_base_url"),
        "/ai_proxy": lambda: cmd_ai_config_edit("/ai_proxy"),
        "/letter": cmd_letter_show,
        "/letter_set": cmd_letter_set_prompt,
        "/search": cmd_search_show,
        "/search_set": cmd_search_set_prompt,
        "/profile_rename": cmd_profile_rename_prompt,
        "/area": cmd_area_prompt,
        "/profile_add": cmd_profile_add_start,
        "/auth": cmd_auth_start,
        "/profiles": cmd_profiles,
    }
    fn = handlers.get(cmd)
    return fn() if fn else None


@mini_app_context.serialized
def handle_callback(chat_id, message_id, data):
    """Нажатие инлайн-кнопки в сообщении-меню."""
    if data.startswith('m:'):
        _, token, action = data.split(':', 2)
        current = _CURRENT_MENU.get(str(chat_id))
        if not current or current[0] != token or current[1] != profiles_lib.get_active_profile()[0] or current[2] < time.monotonic():
            if re.fullmatch(r'ban:[0-9]+',action):
                refresh_ban_confirmation(chat_id,action.split(':',1)[1]); return
            send(chat_id, 'This menu expired. Open /menu again.'); return
        if action.startswith(('cmd:', 'manual:', 'runconfirm:', 'runcancel:', 'del:', 'setres:')):
            _CURRENT_MENU.pop(str(chat_id), None)
        data = action
    if data.startswith(('banconfirm:','bancancel:')):
        item=_BAN_CONFIRM.get(data.split(':',1)[1])
        if not item or item[0]!=str(chat_id) or item[1]!=profiles_lib.get_active_profile()[0] or item[3]<time.monotonic():
            send(chat_id,'Подтверждение устарело или профиль изменился. Нажмите кнопку вакансии ещё раз.'); return
        _BAN_CONFIRM.pop(data.split(':',1)[1],None)
        if data.startswith('bancancel:'):
            send(chat_id,'Добавление в чёрный список отменено.'); return
        send(chat_id,cmd_ban_employer(chat_id,item[2])); return
    if data == 'menu:schedule':
        send_menu(chat_id, schedule_menu(), edit_message_id=message_id); return
    if data.startswith('manual:'):
        return manual_flow.confirm(sys.modules[__name__], chat_id, data)
    if data.startswith(('runconfirm:', 'runcancel:')):
        item = _RUN_CONFIRM.pop(str(chat_id), None)
        token = data.split(':', 1)[1]
        if not item or item[0] != token or item[2] < time.monotonic() or item[1] != profiles_lib.get_active_profile()[0]:
            send(chat_id, 'Кнопка устарела. Откройте меню заново.'); return
        if data.startswith('runcancel:'):
            send_menu(chat_id, menu_by_name('ctl'), edit_message_id=message_id)
            send(chat_id, 'Отклик отменён.'); return
        send(chat_id, _cmd_run_confirmed(chat_id)); return
    if VK and data.startswith("page:"):
        # Меню длиннее шести рядов VK не принимает — листаем страницами.
        try:
            VK.repaginate(chat_id, message_id, int(data[5:]))
        except ValueError:
            pass
        return
    if data == "menu:resume":
        RESUME_PICK["message_id"] = message_id
        _show_resume_change(chat_id, message_id)
        return
    if data.startswith("menu:"):
        send_menu(chat_id, menu_by_name(data[5:]), edit_message_id=message_id)
        return
    if data.startswith("switch:"):
        cmd_profile_switch(data[7:])
        send_menu(chat_id, build_menu_profile(), edit_message_id=message_id)
        return
    if data == "del:yes":
        reply = cmd_profile_remove_active()
        send(chat_id, reply)
        send_menu(chat_id, build_menu_main(), edit_message_id=message_id)
        return
    if data in ("ai:toggle", "ai:dry", "ai:ocr") or data.startswith("aifilter:"):
        pid, profile = profiles_lib.get_active_profile()
        if not profile:
            send_menu(chat_id, build_menu_profile(), edit_message_id=message_id)
            return
        ai = profiles_lib.get_ai(profile)
        try:
            if data == "ai:toggle":
                profiles_lib.update_ai(pid, enabled=not ai["enabled"])
            elif data == "ai:ocr":
                profiles_lib.update_ai(pid, captcha_ocr=not ai['captcha_ocr'])
            elif data == "ai:dry":
                profiles_lib.update_ai(pid, dry_run=not ai["dry_run"])
            else:
                mode = data.split(":", 1)[1]
                if mode not in profiles_lib.AI_FILTER_MODES:
                    mode = ""
                profiles_lib.update_ai(pid, filter=mode)
        except Exception as e:
            send(chat_id, f"⚠️ Ошибка: {html.escape(str(e))}")
        send_menu(chat_id, build_menu_ai(), edit_message_id=message_id)
        return
    if data.startswith("track:"):
        send(chat_id, cmd_track_vacancy(data[6:]))
        return
    if data.startswith("untrack:"):
        pid, _ = profiles_lib.get_active_profile()
        target = data[8:]
        store = load_tracked()
        bucket = store.get(pid or "", {})
        if target == "all":
            store[pid or ""] = {}
        else:
            bucket.pop(target, None)
        save_tracked(store)
        send_menu(chat_id, build_menu_tracked(), edit_message_id=message_id)
        return
    if data.startswith(("flt:", "fset:", "fmt:", "unban:", "ban:")):
        pid, profile = profiles_lib.get_active_profile()
        if not profile:
            send_menu(chat_id, build_menu_profile(), edit_message_id=message_id)
            return
        f = profiles_lib.get_filters(profile)
        try:
            if data.startswith("ban:"):
                send(chat_id, cmd_ban_employer(chat_id, data[4:]))
                return
            if data.startswith("flt:"):
                field = data[4:]
                profiles_lib.update_filters(pid, **{field: not f[field]})
            elif data.startswith("fset:"):
                _, field, value = data.split(":", 2)
                profiles_lib.update_filters(pid, **{field: value})
            elif data.startswith("fmt:"):
                value = data[4:]
                formats = list(f["work_format"])
                formats.remove(value) if value in formats else formats.append(value)
                profiles_lib.update_filters(pid, work_format=formats)
            else:
                target = data[6:]
                if target == "all":
                    profiles_lib.update_filters(pid, excluded_employer_ids=[])
                else:
                    profiles_lib.update_filters(
                        pid, excluded_employer_ids=[e for e in f["excluded_employer_ids"] if e != target])
        except Exception as e:
            send(chat_id, f"⚠️ Ошибка: {html.escape(str(e))}")
        if data.startswith("fmt:"):
            send_menu(chat_id, build_menu_f_format(), edit_message_id=message_id)
        elif data.startswith("unban:"):
            send_menu(chat_id, build_menu_f_banned(), edit_message_id=message_id)
        elif data.startswith("fset:"):
            send_menu(chat_id, build_menu_filters(), edit_message_id=message_id)
        else:
            send_menu(chat_id, build_menu_filters(), edit_message_id=message_id)
        return
    if data == "aicfg:captcha" or data.startswith("aiurl:"):
        _, profile = profiles_lib.get_active_profile()
        if not profile:
            send_menu(chat_id, build_menu_profile(), edit_message_id=message_id)
            return
        account = profile.get("hh_account", "main")
        cfg = read_ai_config(account)
        if data == "aicfg:captcha":
            if cfg["captcha"]:
                hh_config_unset(account, f"{AI_SECTION_CAPTCHA}.api_key")
            elif not cfg["api_key"]:
                send(chat_id, "⚠️ Сначала задай ключ — включать капчу нечем.")
            else:
                for field in ("api_key", "base_url", "model"):
                    if cfg[field]:
                        hh_config_set(account, f"{AI_SECTION_CAPTCHA}.{field}", cfg[field])
        else:
            try:
                _, url = AI_ENDPOINTS[int(data.split(":", 1)[1])]
            except (ValueError, IndexError):
                url = ""
            if url:
                sections = ["openai.default", AI_SECTION_COVER, AI_SECTION_FILTER]
                if cfg["captcha"]:
                    sections.append(AI_SECTION_CAPTCHA)
                errors = ai_config_apply(account, "base_url", url, sections)
                if errors:
                    send(chat_id, "⚠️ " + html.escape("; ".join(errors)))
        send_menu(chat_id, build_menu_ai_access(), edit_message_id=message_id)
        return
    if data.startswith("setres:"):
        if RESUME_PICK.get('message_id') != message_id or RESUME_PICK.get('pid') != profiles_lib.get_active_profile()[0]:
            send(chat_id, 'Кнопка выбора резюме устарела. Откройте выбор заново.'); return
        pid = RESUME_PICK.get("pid")
        resumes = RESUME_PICK.get("resumes") or []
        if not pid:
            send_menu(chat_id, build_menu_profile(), edit_message_id=message_id)
            return
        sel = data[7:]
        if sel == "none":
            resume_id = ""
        else:
            try:
                idx = int(sel)
                resume_id = resumes[idx][0] if 0 <= idx < len(resumes) else ""
            except ValueError:
                resume_id = ""
        try:
            profiles_lib.update_profile(pid, resume_id=resume_id)
        except Exception as e:
            send(chat_id, f"⚠️ Ошибка: {html.escape(str(e))}")
        RESUME_PICK["pid"] = None
        RESUME_PICK["resumes"] = None
        send_menu(chat_id, build_menu_profile(), edit_message_id=message_id)
        return
    if data.startswith("cmd:"):
        reply = run_command(chat_id, data[4:])
        if reply is not None:
            send(chat_id, reply)
        return


# =================== Нижняя панель и обработка сообщений ===================

@mini_app_context.serialized
def handle_message(chat_id, text, user_msg_id):
    """Входящее текстовое сообщение: нижние кнопки, ввод текста, команды."""
    t = (text or "").strip()

    if t == '/ui_status':
        hostname = urllib.parse.urlsplit(API).hostname or 'не задан'
        diagnostic = HTML_BUBBLES.diagnostic_text(classic=str(chat_id) in _CLASSIC_CHATS)
        diagnostic += '\nAPI-сервер: ' + hostname
        diagnostic += '\nТестовая среда UI: ' + ('да' if os.environ.get('TG_API_TEST_ENV') == '1' else 'нет')
        send(chat_id, html.escape(diagnostic))
        return

    # Идёт авторизация — текст уходит в утилиту (кроме /cancel)
    with AUTH_LOCK:
        auth_active = AUTH_DIALOG["active"]
    if auth_active:
        if t == "/cancel":
            send(chat_id, cmd_cancel())
            return
        handle_auth_input(t)
        return

    if t in ('/classic', '/bubbles'):
        PENDING['action'] = None
        PENDING['data'] = None
        if t == '/classic':
            _CLASSIC_CHATS.add(str(chat_id))
            HTML_BUBBLES.active.pop(str(chat_id), None)
        else:
            _CLASSIC_CHATS.discard(str(chat_id))
            HTML_BUBBLES.supported = None
        send_menu(chat_id, build_menu_main(), force_classic=t == '/classic')
        return

    # /cancel — отмена текущего ожидания ввода
    if t == "/cancel":
        send(chat_id, cmd_cancel())
        return

    # /start — поставить нижнюю панель
    if t == "/start":
        PENDING["action"] = None
        PENDING["data"] = None
        tg_delete(chat_id, user_msg_id)
        set_panel(chat_id, "👋 Панель команд внизу. «🎛 Меню» — полное меню.")
        return

    # /menu — открыть инлайн-меню сообщением
    if t == "/menu":
        tg_delete(chat_id, user_msg_id)
        send_menu(chat_id, build_menu_main())
        return

    # Нижние 5 кнопок (работают и во время ожидания ввода — отменяют ввод)
    if t in QUICK_LABELS:
        tg_delete(chat_id, user_msg_id)
        if PENDING["action"]:
            PENDING["action"] = None
            PENDING["data"] = None
        if t == "🎛 Меню":
            send_menu(chat_id, build_menu_main())
        elif t == "▶️ Старт":
            reply = cmd_run(chat_id)
            if reply is not None:
                send(chat_id, reply)
        elif t == "⏸ Пауза":
            send(chat_id, cmd_pause())
            set_panel(chat_id, "⏸ Пауза включена.")
        elif t == "▶️ Возобновить":
            send(chat_id, cmd_resume())
            set_panel(chat_id, "▶️ Рассылка возобновлена.")
        elif t == "👁 Отслеживаемые":
            send_menu(chat_id, build_menu_tracked())
        return

    # Ожидание текстового ввода
    if PENDING["action"]:
        action = PENDING["action"]
        if action == 'schedule_set':
            try:
                from schedule_config import save_hours
                save_hours(t); PENDING['action'] = None
                send(chat_id, 'Расписание сохранено.'); return
            except ValueError:
                send(chat_id, 'Нужны часы 0–23: например 8-21 или 8,12,18.'); return
        if action == "letter_set":
            reply = cmd_letter_save(t)
        elif action == "search_set":
            reply = cmd_search_save(t)
        elif action == "profile_rename":
            reply = cmd_profile_rename_save(t)
        elif action == "area_set":
            reply = cmd_area_save(t)
        elif action == "reply_message":
            reply = cmd_reply_message_save(t)
        elif action == "apply_links":
            reply = cmd_apply_links_run(chat_id, t)
        elif action.startswith("flt_num:"):
            reply = cmd_filter_number_save(action.split(":", 1)[1], t)
        elif action.startswith("aicfg:"):
            field = action.split(":", 1)[1]
            if field == "api_key":
                tg_delete(chat_id, user_msg_id)
            reply = cmd_ai_config_save(field, t)
        elif action.startswith("ai_set:"):
            reply = cmd_ai_save(action.split(":", 1)[1], t)
        elif action.startswith("profile_add:"):
            reply = cmd_profile_add_step(t)
        else:
            reply = None
        if reply is not None:
            send(chat_id, reply)
        return

    # Любой другой текст
    send(chat_id, "Пользуйся кнопками внизу. «🎛 Меню» или /menu — меню.")


def clear_bot_commands():
    """Убирает slash-меню Telegram (пустой список команд)."""
    try:
        data = urllib.parse.urlencode({"commands": json.dumps([])}).encode()
        urllib.request.urlopen(
            f"{API}/setMyCommands",
            data=data, timeout=15
        ).read()
        print("Cleared bot commands (slash menu removed)", flush=True)
    except Exception as e:
        print(f"setMyCommands clear failed: {e}", file=sys.stderr)


def cleanup_stale_auth():
    """При старте бота убираем висящие auth-файлы (если бот рестартанул
    во время диалога авторизации)."""
    for f in (AUTH_REQ, AUTH_OUT, AUTH_IN, AUTH_STATUS, AUTH_CAPTCHA):
        try:
            if f.exists():
                f.unlink()
        except Exception:
            pass
    # Просим helper отменить процесс если он висит
    try:
        AUTH_CANCEL.touch()
    except Exception:
        pass
    with AUTH_LOCK:
        AUTH_DIALOG["active"] = False
        AUTH_DIALOG["profile_id"] = None
        AUTH_DIALOG["hh_account"] = None


def vk_loop():
    """Тот же обработчик команд, но события приходят из VK Long Poll."""
    while True:
        try:
            for event in VK.poll():
                if VK.peer_id and event["chat_id"] != VK.peer_id:
                    continue
                if event["type"] == "callback":
                    VK.answer_callback(event["event_id"], event["user_id"], event["chat_id"])
                    try:
                        handle_callback(event["chat_id"], event["message_id"], event["data"])
                    except Exception as e:
                        import traceback
                        traceback.print_exc()
                        send(event["chat_id"], f"⚠️ Ошибка: {e}")
                elif event["text"]:
                    try:
                        handle_message(event["chat_id"], event["text"], event["message_id"])
                    except Exception as e:
                        import traceback
                        traceback.print_exc()
                        send(event["chat_id"], f"⚠️ Ошибка: {e}")
        except Exception as e:
            print(f"VK poll error: {e}", file=sys.stderr)
            time.sleep(5)


def main():
    print(f"Bot started. Paused: {is_paused()}", flush=True)

    cleanup_stale_auth()
    if not VK:
        clear_bot_commands()

    # Каждому профилю — своё письмо (идемпотентно)
    try:
        profiles_lib.migrate_unique_letters()
    except Exception as e:
        print(f"letter migration failed: {e}", file=sys.stderr)

    global MINI_APP_RUNTIME
    if MINI_APP_URL and not VK:
        MINI_APP_RUNTIME = mini_app.start(sys.modules[__name__])
        configure_mini_app_menu()

    # Ставим нижнюю панель при старте
    try:
        set_panel(TG_CHAT, "🤖 Бот запущен. Панель команд внизу.")
    except Exception as e:
        print(f"initial panel failed: {e}", file=sys.stderr)

    if VK:
        return vk_loop()

    offset = None
    while True:
        try:
            params = {"timeout": 50, "allowed_updates": json.dumps(["message", "callback_query"] + (["widget_query"] if HTML_BUBBLES.wants_updates else []))}
            if offset is not None:
                params["offset"] = offset
            resp = api_call("getUpdates", **params)
            for upd in resp.get("result", []):
                offset = upd["update_id"] + 1

                if upd.get('widget_query'):
                    handle_widget_query(upd['widget_query'])
                    continue

                cb = upd.get("callback_query")
                if cb:
                    cb_chat = str(((cb.get("message") or {}).get("chat") or {}).get("id", ""))
                    if cb_chat != TG_CHAT:
                        continue
                    cb_data = cb.get("data") or ""
                    if not cb_data.startswith('m:'):
                        send(cb_chat, 'This menu expired. Open /menu again.')
                        continue
                    cb_msg_id = (cb.get("message") or {}).get("message_id")
                    try:
                        api_call("answerCallbackQuery", callback_query_id=cb["id"])
                    except Exception:
                        pass
                    try:
                        handle_callback(cb_chat, cb_msg_id, cb_data)
                    except Exception as e:
                        import traceback
                        traceback.print_exc()
                        send(cb_chat, f"⚠️ Ошибка: {html.escape(str(e))}")
                    continue

                msg = upd.get("message") or {}
                chat_id = str((msg.get("chat") or {}).get("id", ""))
                if chat_id != TG_CHAT:
                    continue
                text = msg.get("text") or ""
                if not text:
                    continue
                msg_id = msg.get("message_id")
                try:
                    handle_message(chat_id, text, msg_id)
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    send(chat_id, f"⚠️ Ошибка: {html.escape(str(e))}")
        except Exception as e:
            print(f"poll error: {e}", file=sys.stderr)
            time.sleep(5)


if __name__ == "__main__":
    main()
