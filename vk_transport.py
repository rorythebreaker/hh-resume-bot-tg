#!/usr/bin/env python3
"""VK transport for the bot: same primitives as the Telegram helpers.

The bot talks to VK through a community (group) token with the `messages`
scope. In the community settings enable «Сообщения» and Long Poll API with the
`message_new` and `message_event` events.

Differences from Telegram this module hides:
  * VK renders no HTML — markup is stripped, links are inlined as plain URLs;
  * inline keyboards are limited to 6 rows, so long menus are paginated;
  * button labels are capped at 40 characters and payloads at 255 bytes;
  * callbacks must be acknowledged with messages.sendMessageEventAnswer;
  * photos and documents are uploaded in three steps, not one.
"""
import io
import os
import re
import sys
import html as html_mod
import json
import random
import mimetypes
import urllib.parse
import urllib.request

API_URL = "https://api.vk.com/method"
API_VERSION = "5.199"

MAX_MESSAGE = 4000
MAX_LABEL = 40
MAX_PAYLOAD = 255
MAX_INLINE_ROWS = 6      # ограничение VK на инлайн-клавиатуру
MAX_ROW_BUTTONS = 5


# =================== HTML → плоский текст ===================

_LINK_RE = re.compile(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def html_to_plain(text):
    """Убирает разметку Telegram: VK показывает теги как есть."""
    if not text:
        return ""
    # <a href="url">подпись</a> → «подпись: url», а если подпись и есть url — просто url
    def _link(m):
        url, label = m.group(1), _TAG_RE.sub("", m.group(2)).strip()
        return url if (not label or label == url) else f"{label}: {url}"
    text = _LINK_RE.sub(_link, text)
    text = re.sub(r"</?(b|strong|i|em|u|s|code|pre)>", "", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = _TAG_RE.sub("", text)
    return html_mod.unescape(text)


def _clip(label):
    label = str(label)
    return label if len(label) <= MAX_LABEL else label[:MAX_LABEL - 1] + "…"


# =================== Клавиатуры ===================

def paginate_rows(rows, page=0):
    """Режет меню на страницы по MAX_INLINE_ROWS с навигацией.

    Возвращает (строки_страницы, всего_страниц). Навигационный ряд занимает
    место, поэтому на странице умещается на один пункт меньше."""
    rows = [row[:MAX_ROW_BUTTONS] for row in rows]
    if len(rows) <= MAX_INLINE_ROWS:
        return rows, 1
    per_page = MAX_INLINE_ROWS - 1
    pages = (len(rows) + per_page - 1) // per_page
    page = max(0, min(page, pages - 1))
    chunk = rows[page * per_page:(page + 1) * per_page]
    nav = []
    if page > 0:
        nav.append((f"◀️ {page}/{pages}", f"page:{page - 1}"))
    if page < pages - 1:
        nav.append((f"{page + 2}/{pages} ▶️", f"page:{page + 1}"))
    return chunk + [nav], pages


def build_keyboard(rows, inline=True):
    buttons = []
    for row in rows:
        line = []
        for label, data in row[:MAX_ROW_BUTTONS]:
            payload = json.dumps({"d": data}, ensure_ascii=False)
            if len(payload.encode()) > MAX_PAYLOAD:
                payload = json.dumps({"d": data[:200]}, ensure_ascii=False)
            line.append({
                "color": "secondary",
                "action": {"type": "callback", "label": _clip(label), "payload": payload},
            })
        if line:
            buttons.append(line)
    return {"inline": inline, "buttons": buttons}


def build_panel(labels, columns=2):
    """Нижняя панель: обычные text-кнопки, до 10 рядов."""
    rows, line = [], []
    for label in labels:
        line.append({"color": "primary",
                     "action": {"type": "text", "label": _clip(label),
                                "payload": json.dumps({"t": label}, ensure_ascii=False)}})
        if len(line) == columns:
            rows.append(line)
            line = []
    if line:
        rows.append(line)
    return {"one_time": False, "inline": False, "buttons": rows}


# =================== Транспорт ===================

class VKTransport:
    def __init__(self, token=None, group_id=None, peer_id=None):
        self.token = token or os.environ.get("VK_TOKEN", "")
        self.group_id = str(group_id or os.environ.get("VK_GROUP_ID", ""))
        self.peer_id = str(peer_id or os.environ.get("VK_PEER_ID", ""))
        self._lp = None
        # Меню длиннее шести рядов листаются: помним последнее для каждого чата.
        self._pages = {}

    # ---------- низкий уровень ----------

    def api(self, method, **params):
        params = {k: v for k, v in params.items() if v is not None}
        params.setdefault("access_token", self.token)
        params.setdefault("v", API_VERSION)
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(f"{API_URL}/{method}", data=data)
        with urllib.request.urlopen(req, timeout=70) as r:
            resp = json.loads(r.read())
        if "error" in resp:
            err = resp["error"]
            raise RuntimeError(f"VK {method}: {err.get('error_code')} {err.get('error_msg')}")
        return resp.get("response")

    def _upload(self, upload_url, field, filename, data_bytes):
        boundary = "----vkupload" + os.urandom(8).hex()
        b = boundary.encode()
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        body = b"".join([
            b"--" + b + b"\r\n",
            f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode(),
            f"Content-Type: {ctype}\r\n\r\n".encode(),
            data_bytes, b"\r\n", b"--" + b + b"--\r\n",
        ])
        req = urllib.request.Request(upload_url, data=body)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())

    # ---------- исходящие ----------

    def send_text(self, chat_id, text, keyboard=None):
        text = html_to_plain(text)
        last = None
        while text:
            chunk, text = text[:MAX_MESSAGE], text[MAX_MESSAGE:]
            try:
                last = self.api(
                    "messages.send", peer_id=chat_id, message=chunk,
                    random_id=random.getrandbits(31), dont_parse_links=0,
                    keyboard=json.dumps(keyboard, ensure_ascii=False) if keyboard and not text else None,
                )
            except Exception as e:
                print(f"VK send failed: {e}", file=sys.stderr)
        return last

    def send_menu(self, chat_id, title, rows, edit_message_id=None, page=0):
        rows_page, pages = paginate_rows(rows, page)
        if pages > 1:
            self._pages[str(chat_id)] = rows
        keyboard = json.dumps(build_keyboard(rows_page), ensure_ascii=False)
        text = html_to_plain(title)[:MAX_MESSAGE]
        try:
            if edit_message_id:
                return self.api("messages.edit", peer_id=chat_id,
                                conversation_message_id=edit_message_id,
                                message=text, keyboard=keyboard)
            return self.api("messages.send", peer_id=chat_id, message=text,
                            random_id=random.getrandbits(31), keyboard=keyboard)
        except Exception as e:
            print(f"VK send_menu failed: {e}", file=sys.stderr)
            return None

    def repaginate(self, chat_id, message_id, page):
        """Перерисовывает последнее длинное меню на другой странице."""
        rows = self._pages.get(str(chat_id))
        if not rows:
            return False
        rows_page, _ = paginate_rows(rows, page)
        try:
            self.api("messages.edit", peer_id=chat_id,
                     conversation_message_id=message_id,
                     keyboard=json.dumps(build_keyboard(rows_page), ensure_ascii=False),
                     message="Меню")
            return True
        except Exception as e:
            print(f"VK repaginate failed: {e}", file=sys.stderr)
            return False

    def set_panel(self, chat_id, text, labels):
        return self.send_text(chat_id, text, keyboard=build_panel(labels))

    def send_file(self, chat_id, data_bytes, filename, caption="", as_photo=False):
        try:
            if as_photo:
                server = self.api("photos.getMessagesUploadServer", peer_id=chat_id)
                uploaded = self._upload(server["upload_url"], "photo", filename, data_bytes)
                saved = self.api("photos.saveMessagesPhoto", photo=uploaded["photo"],
                                 server=uploaded["server"], hash=uploaded["hash"])[0]
                attachment = f"photo{saved['owner_id']}_{saved['id']}"
            else:
                server = self.api("docs.getMessagesUploadServer", type="doc", peer_id=chat_id)
                uploaded = self._upload(server["upload_url"], "file", filename, data_bytes)
                saved = self.api("docs.save", file=uploaded["file"], title=filename)
                doc = saved["doc"] if isinstance(saved, dict) and "doc" in saved else saved
                attachment = f"doc{doc['owner_id']}_{doc['id']}"
            return self.api("messages.send", peer_id=chat_id,
                            message=html_to_plain(caption)[:MAX_MESSAGE],
                            attachment=attachment, random_id=random.getrandbits(31))
        except Exception as e:
            print(f"VK upload failed: {e}", file=sys.stderr)
            return None

    def delete_message(self, chat_id, message_id):
        if not message_id:
            return
        try:
            self.api("messages.delete", peer_id=chat_id,
                     cmids=message_id, delete_for_all=1)
        except Exception:
            pass

    def answer_callback(self, event_id, user_id, peer_id):
        try:
            self.api("messages.sendMessageEventAnswer", event_id=event_id,
                     user_id=user_id, peer_id=peer_id)
        except Exception:
            pass

    # ---------- входящие ----------

    def _refresh_longpoll(self):
        resp = self.api("groups.getLongPollServer", group_id=self.group_id)
        self._lp = {"server": resp["server"], "key": resp["key"], "ts": resp["ts"]}

    def poll(self):
        """Один цикл long poll. Возвращает список нормализованных событий:

        {"type": "message"|"callback", "chat_id", "text", "data",
         "message_id", "event_id", "user_id"}
        """
        if not self._lp:
            self._refresh_longpoll()
        url = (f"{self._lp['server']}?act=a_check&key={self._lp['key']}"
               f"&ts={self._lp['ts']}&wait=25")
        with urllib.request.urlopen(url, timeout=40) as r:
            data = json.loads(r.read())

        if data.get("failed"):
            # 1 — устарел ts, 2/3 — протух ключ: переполучаем сервер
            if data["failed"] == 1 and "ts" in data:
                self._lp["ts"] = data["ts"]
            else:
                self._lp = None
            return []

        self._lp["ts"] = data.get("ts", self._lp["ts"])
        events = []
        for update in data.get("updates", []):
            kind = update.get("type")
            obj = update.get("object") or {}
            if kind == "message_new":
                msg = obj.get("message") or obj
                events.append({
                    "type": "message",
                    "chat_id": str(msg.get("peer_id") or ""),
                    "text": msg.get("text") or "",
                    "message_id": msg.get("conversation_message_id"),
                    "data": None, "event_id": None,
                    "user_id": msg.get("from_id"),
                })
            elif kind == "message_event":
                payload = obj.get("payload") or {}
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except Exception:
                        payload = {}
                events.append({
                    "type": "callback",
                    "chat_id": str(obj.get("peer_id") or ""),
                    "text": "",
                    "message_id": obj.get("conversation_message_id"),
                    "data": payload.get("d") or "",
                    "event_id": obj.get("event_id"),
                    "user_id": obj.get("user_id"),
                })
        return events
