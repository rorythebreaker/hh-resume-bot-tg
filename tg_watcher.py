#!/usr/bin/env python3
"""
Daemon: мониторит логи всех HH-аккаунтов и шлёт в TG:
1. Real-time уведомления о каждом успешном отклике
2. Уведомление о протухшем токене (с кнопкой авторизации)

Логи: config/<hh_account>/log.txt для каждого аккаунта из profiles.json.
Также старый config/log.txt (для совместимости при миграции).
"""
import os
import sys
import re
import time
import json
import html
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from storage import atomic_write
from security import redact

sys.path.insert(0, str(Path(__file__).resolve().parent))
import profiles_lib

TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT_ID", "")
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/app/config"))

STATE_FILE = CONFIG_DIR / ".watcher_state.json"

APPLY_RE = re.compile(
    r"201 POST https://api\.hh\.ru/negotiations with params: \{[^}]*'vacancy_id':\s*'(\d+)'"
)
AUTH_ERR_RE = re.compile(r"Refresh token required|Требуется авторизация")

NAME_CACHE = {}

AUTH_ALERT_COOLDOWN = 6 * 3600  # 6 часов дедупликации


TRANSPORT = os.environ.get("BOT_TRANSPORT", "telegram").strip().lower()
VK = None
if TRANSPORT == "vk":
    import vk_transport
    VK = vk_transport.VKTransport()


def send_notification(text, buttons=None):
    """Уведомление в активный транспорт. buttons: [(подпись, callback), ...]."""
    if VK:
        keyboard = vk_transport.build_keyboard([[b] for b in (buttons or [])]) if buttons else None
        return VK.send_text(VK.peer_id, text, keyboard=keyboard)
    markup = None
    if buttons:
        markup = {"inline_keyboard": [[{"text": label, "callback_data": data}]
                                      for label, data in buttons]}
    return tg_send(text, reply_markup=markup)


def tg_send(text, reply_markup=None):
    if not TG_TOKEN or not TG_CHAT:
        return False
    params = {
        "chat_id": TG_CHAT,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    if reply_markup:
        params["reply_markup"] = json.dumps(reply_markup)
    data = urllib.parse.urlencode(params).encode()
    try:
        urllib.request.urlopen(
            os.environ.get("TG_API_ROOT", "https://api.telegram.org").rstrip("/") + f"/bot{TG_TOKEN}/sendMessage",
            data=data, timeout=15).read()
        return True
    except Exception as e:
        print(f"TG send failed: {e}", file=sys.stderr, flush=True)
        return False


def read_token(hh_account):
    from hh_compat import read_access_token
    return read_access_token(CONFIG_DIR, hh_account)


def hh_get_vacancy(vid, hh_account):
    if vid in NAME_CACHE:
        return NAME_CACHE[vid]
    token = read_token(hh_account)
    if not token:
        NAME_CACHE[vid] = ("", "")
        return NAME_CACHE[vid]
    req = urllib.request.Request(
        f"https://api.hh.ru/vacancies/{vid}",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "hh-applicant-tool/watcher",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        name = data.get("name") or ""
        employer = (data.get("employer") or {}).get("name") or ""
        result = (name, employer)
        NAME_CACHE[vid] = result
        if len(NAME_CACHE) > 500:
            for k in list(NAME_CACHE.keys())[:100]:
                NAME_CACHE.pop(k, None)
        return result
    except Exception as e:
        print(f"HH API failed for vacancy {vid}: {e}", file=sys.stderr, flush=True)
        NAME_CACHE[vid] = ("", "")
        return NAME_CACHE[vid]


# =================== Приглашения и здоровье аккаунтов ===================
# Отклики видно в логе, а смену статуса — нет: её знает только API. Поэтому
# раз в INVITE_POLL_INTERVAL опрашиваем /negotiations и сообщаем о новых
# приглашениях. Первый опрос аккаунта только запоминает текущее состояние,
# иначе после старта прилетела бы вся история разом.
INVITE_POLL_INTERVAL = 600      # 10 минут
HEALTH_CHECK_INTERVAL = 21600   # 6 часов
INVITE_STATES = ("invitation",)
SEEN_LIMIT = 500


def hh_api_get(path, hh_account, timeout=20):
    """GET к api.hh.ru. Возвращает (данные, код ошибки|None)."""
    token = read_token(hh_account)
    if not token:
        return None, "no_token"
    req = urllib.request.Request(
        f"https://api.hh.ru{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "hh-applicant-tool/watcher",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        return None, f"http_{e.code}"
    except Exception as e:
        print(f"HH API {path} failed: {e}", file=sys.stderr, flush=True)
        return None, "error"


def notify_invitation(item, hh_account):
    vacancy = item.get("vacancy") or {}
    vid = vacancy.get("id") or ""
    name = vacancy.get("name") or f"Вакансия {vid}"
    employer = (vacancy.get("employer") or {}).get("name") or ""
    title = html.escape(name) + (f" — {html.escape(employer)}" if employer else "")
    url = vacancy.get("alternate_url") or (f"https://hh.ru/vacancy/{vid}" if vid else "")
    lines = [
        "🎉 <b>Приглашение!</b>",
        f"💼 {title}",
        f"👤 Аккаунт: {html.escape(hh_account)}",
    ]
    if url:
        lines.append(f"🔗 <a href=\"{url}\">{url}</a>")
    if VK:
        lines.append("💬 Переписка: https://hh.ru/applicant/negotiations")
        send_notification("\n".join(lines))
    else:
        tg_send("\n".join(lines), reply_markup={"inline_keyboard": [[
            {"text": "💬 Открыть переписку",
             "url": "https://hh.ru/applicant/negotiations"}]]})


def poll_invitations(hh_account, state):
    """Ищет новые приглашения. Возвращает True, если состояние изменилось."""
    seen_key = f"invites_seen_{hh_account}"
    seen = state.get(seen_key)
    first_run = seen is None
    seen = set(seen or [])

    data, err = hh_api_get("/negotiations?per_page=100", hh_account)
    if err == "no_token":
        return False
    if err == "http_401":
        notify_auth_required(hh_account, state)
        return True
    if not data:
        return False

    fresh = []
    current = set()
    for item in data.get("items", []) or []:
        st = (item.get("state") or {}).get("id")
        nid = str(item.get("id") or "")
        if not nid or st not in INVITE_STATES:
            continue
        current.add(nid)
        if nid not in seen:
            fresh.append(item)

    if not first_run:
        for item in fresh:
            notify_invitation(item, hh_account)
    elif current:
        print(f"{hh_account}: seeded {len(current)} invitations, no notifications",
              flush=True)

    merged = list(seen | current)[-SEEN_LIMIT:]
    state[seen_key] = merged
    return True


def check_account_health(hh_account, state):
    """Раз в несколько часов проверяет, жив ли токен аккаунта."""
    data, err = hh_api_get("/me", hh_account)
    if err != "http_401":
        if not err:
            state[f"health_ok_{hh_account}"] = int(time.time())
        return
    notify_auth_required(hh_account, state)


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state):
    try:
        atomic_write(STATE_FILE, json.dumps(state))
    except Exception as e:
        print(f"state save failed: {e}", file=sys.stderr, flush=True)


def notify_apply(vid, hh_account):
    pause_flag = CONFIG_DIR / ".paused"
    if pause_flag.exists():
        print(f"Pause flag set, skipping notification for {vid}", flush=True)
        return
    name, employer = hh_get_vacancy(vid, hh_account)
    url = f"https://hh.ru/vacancy/{vid}"
    if name:
        title = html.escape(name)
        if employer:
            title += f" — {html.escape(employer)}"
    else:
        title = f"Вакансия {vid}"
    text = (
        f"✅ <b>Отклик отправлен</b>\n"
        f"💼 {title}\n"
        f"🔗 <a href=\"{url}\">{url}</a>"
    )
    # Кнопка ведёт в бота: работодателя он определит по вакансии через API
    # и добавит в чёрный список активного профиля.
    send_notification(text, [("👁 Отслеживать", f"track:{vid}"),
                             ("🚫 В чёрный список", f"ban:{vid}")])


def notify_auth_required(hh_account, state):
    key = f"auth_alert_{hh_account}"
    last = state.get(key, 0)
    now = time.time()
    if now - last < AUTH_ALERT_COOLDOWN:
        return
    state[key] = now
    save_state(state)

    affected = profiles_lib.get_profiles_for_account(hh_account)
    names = ", ".join(p.get("display_name") or pid for pid, p in affected) or "—"

    text = (
        f"🛑 <b>HH-токен протух</b>\n\n"
        f"Аккаунт: <code>{html.escape(hh_account)}</code>\n"
        f"Затронуты профили: {html.escape(names)}\n\n"
        f"Отклики не будут отправляться, пока не пройдёшь повторную авторизацию.\n\n"
        f"Нажми кнопку (авторизует активный профиль) или /auth."
    )
    markup = {
        "inline_keyboard": [
            [{"text": "🔑 Авторизовать", "callback_data": "cmd:/auth"}]
        ]
    }
    tg_send(text, reply_markup=markup)


def reset_auth_alert(hh_account, state):
    key = f"auth_alert_{hh_account}"
    if key in state:
        del state[key]
        save_state(state)


def get_inode(path):
    try:
        return path.stat().st_ino
    except Exception:
        return None


def get_log_files():
    result = {}
    accounts = profiles_lib.get_hh_accounts()
    for acc in accounts:
        result[acc] = CONFIG_DIR / acc / "log.txt"
    legacy = CONFIG_DIR / "log.txt"
    if legacy.exists() and not accounts:
        result["__legacy__"] = legacy
    return result


def process_log(hh_account, log_file, state):
    pos_key = f"pos_{hh_account}"
    inode_key = f"inode_{hh_account}"
    buf_key = f"buf_{hh_account}"

    if not log_file.exists():
        return

    current_inode = get_inode(log_file)
    last_pos = state.get(pos_key, None)
    saved_inode = state.get(inode_key)

    if last_pos is None:
        try:
            last_pos = log_file.stat().st_size
        except Exception:
            last_pos = 0
        state[pos_key] = last_pos
        state[inode_key] = current_inode
        save_state(state)
        return

    if saved_inode is not None and saved_inode != current_inode:
        last_pos = 0
        state[inode_key] = current_inode

    try:
        size = log_file.stat().st_size
    except Exception:
        return

    if size < last_pos:
        last_pos = 0

    if size > last_pos:
        buffer = state.get(buf_key, "")
        with log_file.open("r", encoding="utf-8", errors="ignore") as f:
            f.seek(last_pos)
            chunk = f.read()
            last_pos = f.tell()
        buffer += chunk
        if "\n" in buffer:
            lines, _, buffer = buffer.rpartition("\n")
            for line in lines.split("\n"):
                m = APPLY_RE.search(line)
                if m:
                    vid = m.group(1)
                    print(f"[{hh_account}] Detected apply: {vid}", flush=True)
                    try:
                        notify_apply(vid, hh_account)
                    except Exception as e:
                        print(f"notify failed for {vid}: {e}", file=sys.stderr, flush=True)
                    reset_auth_alert(hh_account, state)
                if AUTH_ERR_RE.search(line):
                    print(f"[{hh_account}] Auth required detected", flush=True)
                    try:
                        notify_auth_required(hh_account, state)
                    except Exception as e:
                        print(f"auth notify failed: {e}", file=sys.stderr, flush=True)
        state[buf_key] = buffer
        state[pos_key] = last_pos
        save_state(state)


def main():
    if not (TG_TOKEN and TG_CHAT):
        print("TG credentials missing — exiting", file=sys.stderr)
        return 1
    print("Watcher started (multi-account)", flush=True)
    state = load_state()
    while True:
        try:
            logs = get_log_files()
            for hh_account, log_file in logs.items():
                process_log(hh_account, log_file, state)

            now = time.time()
            accounts = [a for a in logs if a != "__legacy__"]
            if now - state.get("last_invite_poll", 0) >= INVITE_POLL_INTERVAL:
                state["last_invite_poll"] = now
                changed = False
                for hh_account in accounts:
                    changed = poll_invitations(hh_account, state) or changed
                save_state(state)
            if now - state.get("last_health_check", 0) >= HEALTH_CHECK_INTERVAL:
                state["last_health_check"] = now
                for hh_account in accounts:
                    check_account_health(hh_account, state)
                save_state(state)

            time.sleep(2)
        except Exception as e:
            print(f"watcher loop error: {e}", file=sys.stderr, flush=True)
            time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
