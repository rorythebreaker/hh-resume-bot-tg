"""Owner-mediated CAPTCHA relay for background application runs."""
import base64
import json
import os
import secrets
import time
from pathlib import Path
from storage import atomic_write, file_lock


def location():
    return Path(os.environ.get('CONFIG_DIR', '/app/config')) / '.apply-captcha.json'


def pending(profile):
    try:
        data = json.loads(location().read_text(encoding='utf-8'))
        if data['profile'] == profile and data['expires'] > time.time() and not data.get('answered'):
            return data
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def answer(profile, challenge, text):
    if not isinstance(text,str) or not 1 <= len(text.strip()) <= 100:
        raise ValueError('Invalid CAPTCHA answer')
    path=location()
    with file_lock(str(path)+'.lock'):
        data=pending(profile)
        if not data or data['id'] != challenge:
            raise ValueError('CAPTCHA expired')
        atomic_write(str(path)+'.answer',json.dumps({'id':challenge,'text':text.strip()}))
        data['answered']=True
        atomic_write(path,json.dumps(data))


def request(image, profile, timeout=180):
    if not image or len(image)>1024*1024:
        raise ValueError('Invalid CAPTCHA image')
    path=location()
    challenge=secrets.token_hex(16)
    data={'id':challenge,'profile':profile,'expires':time.time()+timeout,
          'image':base64.b64encode(image).decode()}
    atomic_write(path,json.dumps(data))
    try:
        while time.time()<data['expires']:
            try:
                reply=json.loads(Path(str(path)+'.answer').read_text(encoding='utf-8'))
                if reply.get('id')==challenge:
                    if reply.get('text')=='__cancel__':
                        raise RuntimeError('CAPTCHA cancelled by owner')
                    return reply['text']
            except (OSError,ValueError,KeyError):
                pass
            time.sleep(.25)
        raise TimeoutError('CAPTCHA answer not received within 3 minutes')
    finally:
        with file_lock(str(path)+'.lock'):
            for target in (path,Path(str(path)+'.answer')):
                try:
                    if json.loads(target.read_text(encoding='utf-8')).get('id')==challenge:
                        target.unlink()
                except (OSError,ValueError):
                    pass
