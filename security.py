"""Remove credentials from logs and user-facing diagnostics."""
import logging
import re

_PATTERNS = [
    re.compile(r'(Bearer\s+)[^\s\"\']+', re.I),
    re.compile(r'((?:access_token|refresh_token|api_key|password|client_secret)[\"\']?\s*[:=]\s*[\"\']?)[^\s,}\"\']+', re.I),
    re.compile(r'(https?://)[^/@\s]+:[^/@\s]+@', re.I),
    re.compile(r'(api.telegram.org/bot)[^/\s]+', re.I),
]

def redact(text):
    text = str(text)
    for pattern in _PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + '[REDACTED]' + ('@' if m.group(1).endswith('://') else ''), text)
    return text

class SecretFilter(logging.Filter):
    def __init__(self):
        super().__init__()
        self.pending = {}


    def filter(self, record):
        message = record.getMessage()
        # Normalize upstream 2.5.4 response logs to the bot's existing event format.
        match = re.search(r'\((\d+)/(\d+)\) POST https://api\.hh\.ru/negotiations .*vacancy_id.*', message)
        if match:
            vacancy = re.search(r"['\"]vacancy_id['\"]:\s*['\"]?(\d+)", message)
            if vacancy: self.pending[record.thread] = vacancy.group(1)
        response = re.fullmatch(r'(GET|POST|PUT|DELETE) (https://api\.hh\.ru/\S+) (\d{3})', message)
        if response:
            method, url, status = response.groups()
            suffix = ''
            if method == 'POST' and url.endswith('/negotiations'):
                vid = self.pending.pop(record.thread, '')
                suffix = " with params: {'vacancy_id': '" + vid + "'}" if vid else ''
            message = f'{status} {method} {url}' + suffix
        if 'для ключа' in message and any(k in message for k in ('api_key', 'password', 'proxy_url')):
            message = 'Configuration value saved [REDACTED]'
        if any(k in message.lower() for k in ('captchatext', 'captchakey', 'captchastate', 'текст с капчи', 'cookie:', 'cookiejar', 'session headers:', 'set-cookie', 'otpcode', 'auth_code', 'password', 'client_secret', 'access_token', 'refresh_token')):
            message = '[Sensitive authentication diagnostic omitted]'
        record.msg = redact(message)
        record.args = ()
        return True
