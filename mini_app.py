"""Same-process Mini App HTTP API using existing bot actions and Telegram initData."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
from html_bubbles import plain_text
from security import redact
import mini_app_context as context

ASSETS = Path(__file__).with_name('mini_app')
MAX_BODY = 65536
SESSION_TTL = 3600

class AccessError(ValueError):
    pass

def validate_init_data(raw, token, owner, now=None, max_age=3600):
    if not token or not owner or not isinstance(raw, str) or not 1 <= len(raw) <= 8192:
        raise AccessError('Откройте приложение через меню бота.')
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=40)
    except ValueError:
        raise AccessError('Некорректная авторизация Telegram.') from None
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise AccessError('Некорректная авторизация Telegram.')
    received = fields.pop('hash', '')
    check = '\n'.join(k + '=' + v for k, v in sorted(fields.items()))
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if len(received) != 64 or not received.isascii() or not hmac.compare_digest(expected, received):
        raise AccessError('Подпись Telegram не подтверждена.')
    try:
        auth_date = int(fields['auth_date'])
        user = json.loads(fields['user'])
        user_id = user['id']
    except (KeyError, TypeError, ValueError):
        raise AccessError('Недостаточно данных Telegram.') from None
    now = time.time() if now is None else now
    if not -30 <= now - auth_date <= max_age:
        raise AccessError('Сессия истекла. Закройте и заново откройте приложение.')
    if isinstance(user_id, bool) or str(user_id) != str(owner):
        raise AccessError('Эта панель доступна только владельцу бота.')
    return user

def public_url(value):
    parts = urlsplit(value)
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or parts.path not in ('', '/')):
        raise ValueError('TG_MINI_APP_URL must be an HTTPS origin, e.g. https://hh.example.com')
    return value.rstrip('/')

class Session:
    def __init__(self, now):
        self.touched = now
        self.lock = threading.RLock()
        self.menu = None
        self.actions = {}
        self.profile = None
        self.section = "main"
        self.messages = deque(maxlen=30)
        self.input_nonce = None
        self.input_action = None
        self.image = None
        self.message_id = secrets.randbelow(1000000000) + 1000000000
        self.event_id = 0

    def add_text(self, value):
        with self.lock:
            self.event_id += 1
            self.messages.append({'id':self.event_id,'text':plain_text(redact(value))[:8000]})

    def set_menu(self, value, profile=None):
        with self.lock:
            self.menu = json.loads(json.dumps(value))
            self.profile = profile
            self.actions = {}

    def set_captcha(self, png):
        with self.lock:
            if len(png) <= 1024*1024:
                self.image = base64.b64encode(png).decode()

class MiniApp:
    def __init__(self, bot, token, owner, url='', clock=time.time):
        self.bot, self.token, self.owner, self.clock = bot, token, str(owner), clock
        self.url = public_url(url) if url else ''
        self.sessions = {}
        self.lock = context.OPERATION_LOCK

    def session(self, init_data):
        validate_init_data(init_data, self.token, self.owner, now=self.clock())
        key = hashlib.sha256(init_data.encode()).hexdigest()
        now = self.clock()
        with self.lock:
            self.sessions = {k:v for k,v in self.sessions.items() if now-v.touched < SESSION_TTL}
            if key not in self.sessions:
                if len(self.sessions) >= 32:
                    del self.sessions[next(iter(self.sessions))]
                self.sessions[key] = Session(now)
            session = self.sessions[key]
            session.touched = now
        return session

    def snapshot(self, session):
        bot = self.bot
        pid, profile = bot.profiles_lib.get_active_profile()
        with session.lock:
            if session.menu is None or session.profile != pid:
                session.set_menu(bot.build_menu_main())
                session.profile = pid
            if not session.actions:
                for row in session.menu['rows']:
                    for label, payload in row:
                        session.actions[secrets.token_hex(12)] = (label, payload)
            rows = []
            items = iter(session.actions.items())
            for row in session.menu['rows']:
                rows.append([{'id':key,'label':entry[0]} for key,entry in [next(items) for _ in row]])
            action = 'auth_input' if bot.AUTH_DIALOG.get('active') else bot.PENDING.get('action')
            if action:
                if session.input_nonce is None or session.input_action != action:
                    session.input_nonce = secrets.token_hex(12)
                session.input_action = action
            else:
                session.input_nonce = session.input_action = None
                session.image = None
            try:
                limits = bot.compute_limits_state(bot.parse_log_applies(), bot.now_msk())
                free, today = limits['free_now'], limits['calendar_today']
            except (OSError, ValueError, KeyError):
                free, today = None, None
            return {'section':session.section, 'menu':{'title':plain_text(session.menu['title']),'rows':rows},
                    'profile':{'name':(profile or {}).get('display_name') or pid or 'Создайте профиль',
                               'account':(profile or {}).get('hh_account','—')},
                    'paused':bot.is_paused(), 'stats':{'free':free,'today':today},
                    'messages':list(session.messages),
                    'input':{'action':action,'nonce':session.input_nonce,
                             'secret':bool(action and (action.startswith('aicfg:api_key') or action=='auth_input'))} if action else None,
                    'captcha':session.image if action=='auth_input' else None,
                    'notifications':'Сводки и уведомления продолжают приходить в чат бота.'}

    def request(self, init_data, route, body=None):
        session = self.session(init_data)
        bot = self.bot
        with self.lock, context.capture(session):
            if route == '/api/state':
                pass
            elif route == '/api/view':
                name = (body or {}).get('name')
                if name not in ('main','ctl','view','profile','filters','ai','schedule'):
                    raise ValueError('Неизвестный раздел.')
                menu = bot.schedule_menu() if name=='schedule' else bot.menu_by_name(name)
                session.set_menu(menu)
                session.section = name if name in ('profile','filters','ai','ctl') else ('ctl' if name=='schedule' else 'main')
                session.profile = bot.profiles_lib.get_active_profile()[0]
            elif route == '/api/action':
                with session.lock:
                    if session.profile != bot.profiles_lib.get_active_profile()[0]:
                        raise ValueError('Профиль изменился. Обновите панель.')
                    entry = session.actions.get((body or {}).get('id'))
                    if not entry:
                        raise ValueError('Кнопка устарела или уже использована. Обновите панель.')
                    session.actions = {}
                if entry[1].startswith('menu:'):
                    destination = entry[1].split(':',1)[1]
                    if destination in ('profile','switch','del','resume'):
                        session.section = 'profile'
                    elif destination == 'filters' or destination.startswith('f_'):
                        session.section = 'filters'
                    elif destination == 'ai' or destination.startswith('ai_'):
                        session.section = 'ai'
                    elif destination in ('ctl','schedule'):
                        session.section = 'ctl'
                    elif destination in ('main','view','help','tracked'):
                        session.section = 'main'
                bot.handle_callback(self.owner, session.message_id, entry[1])
            elif route == '/api/input':
                with session.lock:
                    nonce = (body or {}).get('nonce')
                    text = (body or {}).get('text')
                    if (not session.input_nonce or nonce != session.input_nonce
                            or not isinstance(text,str) or not 1 <= len(text) <= 12000):
                        raise ValueError('Форма устарела. Обновите панель и повторите.')
                    current_action = 'auth_input' if bot.AUTH_DIALOG.get('active') else bot.PENDING.get('action')
                    if (not current_action or current_action != session.input_action
                            or session.profile != bot.profiles_lib.get_active_profile()[0]):
                        raise ValueError('Форма или профиль изменились. Обновите панель.')
                    session.input_nonce = None
                bot.handle_message(self.owner,text,None)
            elif route == '/api/cancel':
                bot.send(self.owner,bot.cmd_cancel())
                session.set_menu(bot.build_menu_main())
                session.profile = bot.profiles_lib.get_active_profile()[0]
            else:
                raise KeyError(route)
            return self.snapshot(session)

def make_server(app, host='127.0.0.1', port=8080):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass  # initData and form inputs never enter access logs.
        def respond(self, status, value, content_type='application/json; charset=utf-8'):
            data = json.dumps(value,ensure_ascii=False).encode() if isinstance(value,dict) else value
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors https://web.telegram.org https://*.telegram.org")
            self.end_headers()
            self.wfile.write(data)
        def api(self):
            raw = self.headers.get('Authorization','')
            if not raw.startswith('tma '):
                self.respond(401,{'error':'Откройте приложение через Telegram.'});return
            body = None
            route = urlsplit(self.path).path
            try:
                if self.command == 'POST':
                    length = int(self.headers.get('Content-Length','0'))
                    if not 0 < length <= MAX_BODY:
                        self.respond(413,{'error':'Слишком большой запрос.'});return
                    if self.headers.get_content_type() != 'application/json':
                        self.respond(415,{'error':'Нужен JSON.'});return
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body,dict): raise ValueError('Нужен объект JSON.')
                elif route != '/api/state':
                    self.respond(405,{'error':'Нужен POST.'});return
                self.respond(200,app.request(raw[4:],route,body))
            except AccessError as exc:
                self.respond(401,{'error':str(exc)})
            except (ValueError,UnicodeDecodeError) as exc:
                self.respond(409,{'error':'Действие не выполнено. Обновите панель и проверьте введённые данные.'})
            except KeyError:
                self.respond(404,{'error':'Раздел не найден.'})
            except Exception:
                self.respond(500,{'error':'Не удалось выполнить действие. Проверьте состояние бота.'})
        def do_GET(self):
            path = urlsplit(self.path).path
            if path.startswith('/api/'): return self.api()
            if path == '/health': return self.respond(200,{'ok':True})
            files={'/':('index.html','text/html; charset=utf-8'),
                   '/app.mjs':('app.mjs','text/javascript; charset=utf-8'),
                   '/app.css':('app.css','text/css; charset=utf-8')}
            if path not in files: return self.respond(404,{'error':'Not found'})
            name,mime=files[path]
            self.respond(200,(ASSETS/name).read_bytes(),mime)
        def do_POST(self):
            if not urlsplit(self.path).path.startswith('/api/'):
                return self.respond(404,{'error':'Not found'})
            self.api()
    server = ThreadingHTTPServer((host,port),Handler)
    server.daemon_threads = True
    return server

def start(bot):
    url = os.environ.get('TG_MINI_APP_URL','').strip()
    if not url: return None
    app = MiniApp(bot,bot.TG_TOKEN,bot.TG_CHAT,url)
    server = make_server(app,os.environ.get('TG_MINI_APP_HOST','0.0.0.0'),
                         int(os.environ.get('TG_MINI_APP_PORT','8080')))
    thread = threading.Thread(target=server.serve_forever,daemon=True,name='mini-app-http')
    thread.start()
    return app,server
