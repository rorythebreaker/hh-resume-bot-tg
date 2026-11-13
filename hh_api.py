"""Account-scoped API requests. Retry reads only; never blindly retry applications."""
import json
import os
import subprocess
import time
import urllib.request
import urllib.error
import urllib.parse
from hh_compat import read_access_token, cli_args

class HHAPIError(RuntimeError):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail
        super().__init__(detail)

def error_message(status, payload=None):
    payload = payload if isinstance(payload, dict) else {}
    values = [str(e.get('value', '')) for e in payload.get('errors', []) if isinstance(e, dict)]
    if any('captcha' in v.lower() for v in values):
        return 'HTTP 403: требуется CAPTCHA; пройдите проверку HH и возобновите отклики'
    messages = {
        400: 'неверные параметры, обязательное письмо, анкета или повторный отклик; проверьте вакансию на HH',
        401: 'истёк или недействителен токен; авторизуйте аккаунт заново',
        403: 'доступ ограничен HH; возможна CAPTCHA или проверка аккаунта',
        404: 'вакансия не найдена или недоступна',
        429: 'превышена частота запросов HH; повторите позже',
    }
    return f'HTTP {status}: ' + messages.get(status, 'временная ошибка HH' if status >= 500 else 'ошибка запроса HH')

def _post_with_captcha(account, path, fields):
    """Delegate CAPTCHA challenge handling to upstream's API client."""
    from hh_cli import BotTool
    from hh_applicant_tool.tool import BaseNamespace
    from hh_applicant_tool.api.errors import ApiError
    import requests
    tool = BotTool()
    args = tool._parser.parse_args(['--config-dir', os.environ.get('CONFIG_DIR', '/app/config'),
        '--profile', account, '--no-use-ai', 'whoami'], namespace=BaseNamespace())
    tool._assign_args(args)
    previous = os.environ.get('HH_BOT_CAPTCHA_UI')
    os.environ['HH_BOT_CAPTCHA_UI'] = '1'
    try:
        client = tool.api_client
        client.max_retries = 1  # Never replay ambiguous application POSTs.
        result = client.post(path, fields or {})
        tool._application_succeeded()
        return result
    except requests.RequestException:
        raise HHAPIError(0, 'Соединение с HH прервано; результат отправки неизвестен. Проверьте отклики перед повтором.') from None
    except ApiError as exc:
        status = getattr(exc, 'status_code', 0)
        raise HHAPIError(status, error_message(status, getattr(exc, '_data', None))) from None
    except SystemExit as exc:
        reasons = {4: 'Время ожидания CAPTCHA истекло.', 5: 'Ввод CAPTCHA отменён.',
                   6: 'Превышено число попыток CAPTCHA.'}
        raise HHAPIError(403, reasons.get(exc.code, 'Проверка CAPTCHA прервана.')) from None
    finally:
        tool._captcha_run_finished()
        try:
            tool.save_cookies()
        except (OSError, ValueError):
            pass
        if previous is None:
            os.environ.pop('HH_BOT_CAPTCHA_UI', None)
        else:
            os.environ['HH_BOT_CAPTCHA_UI'] = previous


def request(account, path, method='GET', fields=None):
    if method.upper() == 'POST':
        return _post_with_captcha(account, path, fields)
    root = os.environ.get('CONFIG_DIR', '/app/config')
    refreshed = False
    for attempt in range(3):
        token = read_access_token(root, account)
        if not token:
            raise HHAPIError(401, error_message(401))
        headers = {'Authorization': f'Bearer {token}', 'User-Agent': 'hh-resume-bot-tg/2.5.4', 'Accept': 'application/json'}
        data = None
        if fields is not None:
            data = urllib.parse.urlencode(fields).encode('utf-8')
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        req = urllib.request.Request('https://api.hh.ru/' + path.lstrip('/'), data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            try: payload = json.loads(exc.read())
            except (ValueError, UnicodeDecodeError): payload = {}
            if exc.code == 401 and method == 'GET' and not refreshed:
                refreshed = True
                try:
                    result = subprocess.run(cli_args(account, '--no-use-ai', '--no-handle-captcha', 'whoami'), capture_output=True, timeout=60)
                    if result.returncode == 0: continue
                except (OSError, subprocess.TimeoutExpired): pass
            if method == 'GET' and exc.code >= 500 and attempt < 2:
                time.sleep(2 ** attempt); continue
            raise HHAPIError(exc.code, error_message(exc.code, payload)) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if method == 'GET' and attempt < 2:
                time.sleep(2 ** attempt); continue
            raise HHAPIError(0, 'Ошибка соединения с HH; результат отправки неизвестен. Проверьте отклики на сайте перед повтором.') from None
        except (ValueError, UnicodeDecodeError):
            raise HHAPIError(0, 'HH вернул некорректный JSON; проверьте результат на сайте') from None
    raise HHAPIError(401, error_message(401))
