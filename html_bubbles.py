"""Experimental Telegram HTMLBubbles transport; existing callbacks remain authoritative.

Contract: TelegramMessenger/widget-sandbox docs/tgw-format.md and reference test-bot.ts.
No public/shared state, no forward queries, no alternative application dispatcher.
"""
import html
import json
import logging
import secrets
import time
import urllib.error
import zlib
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

LOG = logging.getLogger(__name__)
ASSET = Path(__file__).with_name('ui') / 'menu_widget.html'
PAGE_SIZE = 5

class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
    def handle_data(self, text):
        self.parts.append(text)
    def handle_starttag(self, tag, attrs):
        if tag == 'br': self.parts.append('\n')

def plain_text(text):
    parser = _PlainText()
    parser.feed(text)
    return ''.join(parser.parts)

class WidgetAPIError(RuntimeError):
    def __init__(self, code, description):
        self.code = code
        self.description = description
        super().__init__('Widget API request failed')

@dataclass
class MenuSession:
    nonce: str
    profile: str | None
    title: str
    buttons: list
    expires: float
    page: int = 0

class HtmlBubbles:
    def __init__(self, api, mode='auto', clock=time.monotonic):
        if mode not in ('auto', 'off', 'on'):
            raise ValueError('TG_HTML_BUBBLES must be auto, on or off')
        self.api, self.mode, self.clock = api, mode, clock
        self.supported = None
        self.sessions = {}
        self.seen = {}
        self.active = {}

    @property
    def wants_updates(self):
        return self.mode != 'off' and self.supported is True

    def owns(self, chat_id, message_id):
        return (str(chat_id), message_id) in self.sessions

    def _call(self, method, **params):
        response = self.api(method, **params)
        if not isinstance(response, dict) or not response.get('ok'):
            raise WidgetAPIError(response.get('error_code', 0) if isinstance(response, dict) else 0,
                                 str(response.get('description', '')) if isinstance(response, dict) else '')
        return response.get('result')

    def _prune(self):
        now = self.clock()
        self.sessions = {k: v for k, v in self.sessions.items() if v.expires > now}
        self.seen = {k: expiry for k, expiry in self.seen.items() if expiry > now}

    def _state(self, session):
        buttons = session.buttons[session.page * PAGE_SIZE:(session.page + 1) * PAGE_SIZE]
        state = {'n': session.nonce, 't': session.title,
                 'p': session.page, 'c': max(1, (len(session.buttons)+PAGE_SIZE-1)//PAGE_SIZE),
                 'b': [{'i': session.page*PAGE_SIZE+i, 't': button['text'][:44]} for i, button in enumerate(buttons)]}
        def encode():
            return json.dumps(state, ensure_ascii=False, separators=(',', ':'))
        # Fit private state to the documented UTF-8 byte budget, not Python character count.
        while len(encode().encode('utf-8')) > 1024:
            candidates = [(state, 't')] + [(b, 't') for b in state['b']]
            target, key = max(candidates, key=lambda pair: len(pair[0][pair[1]]))
            if len(target[key]) < 8: raise ValueError('Widget state cannot fit')
            target[key] = target[key][:-8]
        return encode()

    def try_menu(self, chat_id, title, keyboard, profile, message_id=None):
        is_widget = self.owns(chat_id, message_id)
        if self.mode == 'off' or self.supported is False:
            return False
        if message_id is not None and not is_widget:
            return False
        text = plain_text(title)
        # Long help/configuration text keeps the full classic view.
        if len(text) > 260:
            return False
        buttons = [dict(b) for row in keyboard['inline_keyboard'] for b in row]
        session = MenuSession(secrets.token_hex(4), profile, text, buttons, self.clock()+1800)
        try:
            if is_widget:
                result = self._call('editMessageWidgetData', chat_id=chat_id, message_id=message_id,
                                    chat_data=self._state(session))
            else:
                source = ASSET.read_text(encoding='utf-8')
                if len(source.encode('utf-8')) > 32768 or len(zlib.compress(source.encode('utf-8'), 9)) > 4096:
                    raise ValueError('Widget asset exceeds Telegram payload limits')
                result = self._call('sendWidget', chat_id=chat_id, html=source, width=0, height=0,
                                    allow_queries='true', is_shared='false',
                                    chat_data=self._state(session),
                                    caption='HH Resume Bot · /menu — меню · /classic — обычные кнопки')
                if not isinstance(result, dict) or not isinstance(result.get('message_id'), int):
                    raise ValueError('sendWidget did not return a Message')
                message_id = result['message_id']
        except (urllib.error.HTTPError, WidgetAPIError) as exc:
            code = exc.code
            if code in (400, 404):
                self.supported = False
                LOG.info('HTMLBubbles unavailable (HTTP %s); using classic menus', code)
                return False
            # Avoid a duplicate message after rate limiting or an ambiguous API error.
            LOG.warning('Widget request failed (HTTP %s); not retried', code)
            return True
        except (urllib.error.URLError, TimeoutError, OSError):
            LOG.warning('Widget delivery outcome unknown; not retried')
            return True
        self._prune()
        self.sessions[(str(chat_id), message_id)] = session
        self.active[str(chat_id)] = session.nonce
        self.supported = True
        return True

    def handle_query(self, query, owner_chat, profile, dispatch, refresh):
        self._prune()
        qid = query.get('id')
        if not isinstance(qid, str): return
        def answer(text):
            try:
                self._call('answerWidgetQuery', widget_query_id=qid, data=text)
            except (urllib.error.URLError, OSError, WidgetAPIError):
                LOG.warning('Could not acknowledge widget query')
        chat = query.get('chat') or {}
        sender = query.get('from') or {}
        key = (str(chat.get('id', '')), query.get('message_id'))
        session = self.sessions.get(key)
        data = query.get('data')
        if (self.mode == 'off' or chat.get('type') != 'private' or key[0] != str(owner_chat)
                or str(sender.get('id', '')) != str(owner_chat) or query.get('inline_message_id')
                or not isinstance(data, str) or not 1 <= len(data.encode('utf-8')) <= 64
                or not session or session.profile != profile
                or self.active.get(key[0]) != session.nonce):
            answer('Меню устарело. Откройте /menu.'); return
        parts = data.split(':')
        if len(parts) != 3 or parts[:2] != ['hb', session.nonce]:
            answer('Меню устарело. Откройте /menu.'); return
        if qid in self.seen:
            answer('Действие уже обработано.'); return
        self.seen[qid] = self.clock()+1800
        # Bound memory if an authorized client sends many requests.
        if len(self.seen) > 512: self.seen.pop(next(iter(self.seen)))
        action = parts[2]
        if action.startswith('p') and action[1:].isdigit():
            page = int(action[1:])
            if 0 <= page < max(1, (len(session.buttons)+PAGE_SIZE-1)//PAGE_SIZE):
                session.page = page
                try:
                    self._call('editMessageWidgetData', chat_id=key[0], message_id=key[1],
                               chat_data=self._state(session))
                    answer('')
                except (urllib.error.URLError, OSError, WidgetAPIError):
                    answer('Не удалось обновить меню. Попробуйте позже.')
                return
        elif action == 'r':
            answer('')
            refresh(key[0], key[1])
            return
        elif action.startswith('a') and action[1:].isdigit():
            index = int(action[1:])
            if (0 <= index < len(session.buttons)
                    and session.page*PAGE_SIZE <= index < (session.page+1)*PAGE_SIZE):
                payload = session.buttons[index]['callback_data']
                answer('')
                dispatch(key[0], key[1], payload)
                return
        answer('Откройте /menu заново.')
