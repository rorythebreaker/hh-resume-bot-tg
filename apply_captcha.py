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
    event(profile,'waiting','HH запросил CAPTCHA. Введите текст с картинки.')
    notify_owner()
    try:
        while time.time()<data['expires']:
            try:
                reply=json.loads(Path(str(path)+'.answer').read_text(encoding='utf-8'))
                if reply.get('id')==challenge:
                    if reply.get('text')=='__cancel__':
                        raise RuntimeError('CAPTCHA cancelled by owner')
                    event(profile,'submitting','Ответ получен. Отправляем его в HH.')
                    return reply['text']
            except (OSError,ValueError,KeyError):
                pass
            time.sleep(.25)
        event(profile,'expired','Время ожидания CAPTCHA истекло: ответ не получен за три минуты.')
        raise TimeoutError('CAPTCHA answer not received within 3 minutes')
    finally:
        with file_lock(str(path)+'.lock'):
            for target in (path,Path(str(path)+'.answer')):
                try:
                    if json.loads(target.read_text(encoding='utf-8')).get('id')==challenge:
                        target.unlink()
                except (OSError,ValueError):
                    pass


def status(profile):
    try:
        value=json.loads(Path(str(location())+'.status').read_text(encoding='utf-8'))
        if value.get('profile')==profile and time.time()-value.get('time',0)<900:
            return value
    except (OSError,ValueError,TypeError):
        pass
    return None


def event(profile, state, message):
    value={'profile':profile,'state':state,'message':message,'time':time.time()}
    atomic_write(str(location())+'.status',json.dumps(value))
    print('CAPTCHA_STATUS: '+message,flush=True)


def notify_owner():
    """Notify about a live challenge, without exposing image keys or answers."""
    from urllib.request import Request, urlopen
    token=os.environ.get('TG_BOT_TOKEN','')
    owner=os.environ.get('TG_CHAT_ID','')
    if not token or not owner or os.environ.get('BOT_TRANSPORT','telegram') != 'telegram':
        return
    payload={'chat_id':owner,'text':'HH запросил CAPTCHA. Откройте Mini App и введите текст с картинки в течение трёх минут. Прогон ожидает ваш ответ.'}
    url=os.environ.get('TG_MINI_APP_URL','')
    if url.startswith('https://'):
        payload['reply_markup']={'inline_keyboard':[[{'text':'Открыть Mini App','web_app':{'url':url}}]]}
    root=os.environ.get('TG_API_ROOT','https://api.telegram.org').rstrip('/')
    try:
        request=Request(root+'/bot'+token+'/sendMessage',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=10) as response:
            response.read()
    except Exception:
        print('CAPTCHA notification unavailable; open Mini App to check the challenge.',flush=True)
