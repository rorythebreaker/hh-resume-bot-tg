import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode
from unittest.mock import Mock
import pytest
import mini_app
import mini_app_context as context

TOKEN = 'unit-test-token'
OWNER = '123'

def signed(user=123, auth_date=None, extra=None):
    fields={'auth_date':str(int(time.time()) if auth_date is None else auth_date),
            'query_id':'test-session','user':json.dumps({'id':user,'first_name':'Owner'},separators=(',',':'))}
    fields.update(extra or {})
    check=chr(10).join(k+'='+v for k,v in sorted(fields.items()))
    secret=hmac.new(b'WebAppData',TOKEN.encode(),hashlib.sha256).digest()
    fields['hash']=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    return urlencode(fields)

@pytest.fixture
def app(workspace,monkeypatch):
    import tg_bot as bot
    root,_=workspace
    monkeypatch.setattr(bot,'TG_CHAT',OWNER)
    monkeypatch.setattr(bot,'TG_TOKEN',TOKEN)
    monkeypatch.setattr(bot,'CONFIG_DIR',root/'config')
    monkeypatch.setattr(bot,'PAUSE_FLAG',root/'config'/'.paused')
    monkeypatch.setattr(bot,'PID_FILE',root/'config'/'.apply.pid')
    for key,name in [('AUTH_REQ','.auth_request'),('AUTH_IN','.auth_input'),('AUTH_OUT','.auth_output'),('AUTH_STATUS','.auth_status'),('AUTH_CANCEL','.auth_cancel'),('AUTH_CAPTCHA','.auth_captcha.png')]:
        monkeypatch.setattr(bot,key,root/'config'/name)
    monkeypatch.setattr(bot,'PENDING',{'action':None,'data':None})
    monkeypatch.setattr(bot,'AUTH_DIALOG',{'active':False})
    monkeypatch.setattr(bot,'api_call',Mock(return_value={'ok':True,'result':True}))
    return mini_app.MiniApp(bot,TOKEN,OWNER,'https://hh.example.com')

def action_id(session,payload):
    return next(key for key,entry in session.actions.items() if entry[1]==payload)

def test_valid_signature_and_owner():
    assert mini_app.validate_init_data(signed(),TOKEN,OWNER)['id']==123

@pytest.mark.parametrize('raw',[lambda:signed().replace('Owner','Attacker'),lambda:signed(user=999),lambda:signed(auth_date=1),lambda:signed(auth_date=int(time.time())+60),lambda:signed()+'&user=x',lambda:'',lambda:'a'*8193])
def test_invalid_init_data_rejected(raw):
    with pytest.raises(mini_app.AccessError): mini_app.validate_init_data(raw(),TOKEN,OWNER)

def test_static_url_requires_https_and_no_credentials():
    assert mini_app.public_url('https://hh.example.com/')=='https://hh.example.com'
    for url in ['http://example.com','https://user:secret@example.com','https://example.com/path','https://example.com/?token=x']:
        with pytest.raises(ValueError): mini_app.public_url(url)

def test_state_has_owner_profile_and_all_menu_buttons(app):
    state=app.request(signed(),'/api/state')
    assert state['profile']['name']=='Test'
    assert sum(map(len,state['menu']['rows']))==4
    assert TOKEN not in json.dumps(state)

def test_generic_menu_actions_are_available_in_app(app):
    raw=signed()
    app.request(raw,'/api/view',{'name':'profile'})
    session=app.session(raw)
    expected=app.bot.build_menu_profile()
    assert [v[1] for v in session.actions.values()]==[payload for row in expected['rows'] for _,payload in row]

def test_unknown_action_cannot_invoke_bot_handler(app,monkeypatch):
    dispatch=Mock();monkeypatch.setattr(app.bot,'handle_callback',dispatch)
    app.request(signed(),'/api/state')
    with pytest.raises(ValueError): app.request(signed(),'/api/action',{'id':'cmd:/run'})
    dispatch.assert_not_called()

def test_bulk_launch_requires_confirmation_in_app(app,monkeypatch):
    run=Mock(return_value='started');monkeypatch.setattr(app.bot,'_cmd_run_confirmed',run)
    raw=signed();app.request(raw,'/api/view',{'name':'ctl'})
    session=app.session(raw);first=action_id(session,'cmd:/run')
    state=app.request(raw,'/api/action',{'id':first})
    run.assert_not_called()
    confirm=next(k for k,v in session.actions.items() if v[1].startswith('runconfirm:'))
    app.request(raw,'/api/action',{'id':confirm})
    run.assert_called_once_with(OWNER)
    with pytest.raises(ValueError): app.request(raw,'/api/action',{'id':confirm})
    app.bot.api_call.assert_not_called()

def test_profile_change_invalidates_old_controls(app):
    raw=signed();app.request(raw,'/api/view',{'name':'ctl'})
    session=app.session(raw);key=action_id(session,'cmd:/pause')
    other=app.bot.profiles_lib.add_profile('Other','other','sre','letter_default.txt')
    app.bot.profiles_lib.set_active_profile(other)
    with pytest.raises(ValueError): app.request(raw,'/api/action',{'id':key})
    assert not app.bot.is_paused()

def test_form_submission_is_single_use(app):
    raw=signed();app.request(raw,'/api/view',{'name':'profile'})
    session=app.session(raw)
    state=app.request(raw,'/api/action',{'id':action_id(session,'cmd:/search_set')})
    nonce=state['input']['nonce']
    state=app.request(raw,'/api/input',{'nonce':nonce,'text':'devops'})
    assert state['input'] is None
    assert app.bot.profiles_lib.get_active_profile()[1]['search']=='devops'
    with pytest.raises(ValueError): app.request(raw,'/api/input',{'nonce':nonce,'text':'bad'})
    app.bot.api_call.assert_not_called()

def test_pause_is_effective_without_control_messages_in_chat(app):
    raw=signed();app.request(raw,'/api/view',{'name':'ctl'})
    session=app.session(raw)
    app.request(raw,'/api/action',{'id':action_id(session,'cmd:/pause')})
    assert app.bot.is_paused()
    app.bot.api_call.assert_not_called()

def test_background_notifications_still_go_to_chat_and_prompts_stay_in_app(app):
    session=app.session(signed())
    with context.capture(session):
        app.bot.send(OWNER,'UI prompt')
        worker=context.make_thread(target=lambda:app.bot.send(OWNER,'Background summary'),daemon=True)
        worker.start();worker.join(2)
    assert [m['text'] for m in session.messages]==['UI prompt','Background summary']
    app.bot.api_call.assert_called_once()
    assert app.bot.api_call.call_args.kwargs['text']=='Background summary'

def test_async_manual_preview_stays_in_app(app,monkeypatch):
    monkeypatch.setattr(app.bot,'hh_get_strict',lambda path:{'id':'123456','name':'Developer'} if '/vacancies/' in path else {})
    raw=signed();app.request(raw,'/api/view',{'name':'ctl'})
    session=app.session(raw)
    state=app.request(raw,'/api/action',{'id':action_id(session,'cmd:/apply_links')})
    app.request(raw,'/api/input',{'nonce':state['input']['nonce'],'text':'https://hh.ru/vacancy/123456'})
    for _ in range(40):
        with session.lock:
            if any(v[1].startswith('manual:') for v in session.actions.values()): break
        time.sleep(.01);app.request(raw,'/api/state')
    assert any(v[1].startswith('manual:') for v in session.actions.values())
    assert all(call.args[0]!='sendWidget' for call in app.bot.api_call.call_args_list)

def test_captcha_captured_only_for_owner_session(app):
    raw=signed();session=app.session(raw)
    app.bot.AUTH_DIALOG['active']=True
    with context.capture(session): context.captcha(b'png-test')
    assert app.request(raw,'/api/state')['captcha'] is not None
    with pytest.raises(mini_app.AccessError): app.request(signed(user=999),'/api/state')

@pytest.fixture
def http_app(app):
    server=mini_app.make_server(app,port=0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield 'http://127.0.0.1:'+str(server.server_port)
    server.shutdown();server.server_close();thread.join(2)

def test_http_requires_authorization_and_blocks_path_traversal(http_app):
    for route,status in [('/api/state',401),('/../profiles.json',404)]:
        with pytest.raises(urllib.error.HTTPError) as error: urllib.request.urlopen(http_app+route)
        assert error.value.code==status

def test_http_signed_state_and_no_cache(http_app):
    req=urllib.request.Request(http_app+'/api/state',headers={'Authorization':'tma '+signed()})
    with urllib.request.urlopen(req) as response:
        assert response.headers['Cache-Control']=='no-store'
        assert 'Access-Control-Allow-Origin' not in response.headers
        assert json.load(response)['profile']['name']=='Test'

def test_public_shell_contains_no_bot_credentials(http_app):
    with urllib.request.urlopen(http_app+'/') as response:
        text=response.read().decode()
        assert TOKEN not in text and 'telegram-web-app.js' in text

def test_input_rejects_a_changed_prompt(app):
    raw=signed();app.request(raw,'/api/view',{'name':'profile'})
    session=app.session(raw)
    state=app.request(raw,'/api/action',{'id':action_id(session,'cmd:/search_set')})
    app.bot.PENDING['action']='letter_set'
    with pytest.raises(ValueError):
        app.request(raw,'/api/input',{'nonce':state['input']['nonce'],'text':'wrong destination'})

def test_input_rejects_a_changed_profile(app):
    raw=signed();app.request(raw,'/api/view',{'name':'profile'})
    session=app.session(raw)
    state=app.request(raw,'/api/action',{'id':action_id(session,'cmd:/search_set')})
    other=app.bot.profiles_lib.add_profile('Other','other','sre','letter_default.txt')
    app.bot.profiles_lib.set_active_profile(other)
    with pytest.raises(ValueError):
        app.request(raw,'/api/input',{'nonce':state['input']['nonce'],'text':'wrong profile'})

def test_native_menu_opens_app_without_chat_control_buttons(app,monkeypatch):
    bot=app.bot
    monkeypatch.setattr(bot,'MINI_APP_URL','https://hh.example.com')
    bot.configure_mini_app_menu()
    call=bot.api_call.call_args_list[0]
    assert call.args[0]=='setChatMenuButton'
    assert json.loads(call.kwargs['menu_button'])['web_app']['url']=='https://hh.example.com'
    bot.send_menu(OWNER,bot.build_menu_main())
    markup=json.loads(bot.api_call.call_args.kwargs['reply_markup'])
    assert markup['inline_keyboard']==[[{'text':'Открыть панель','web_app':{'url':'https://hh.example.com'}}]]

def test_profile_switch_keeps_profile_screen(app):
    raw=signed()
    other=app.bot.profiles_lib.add_profile('Other','other','sre','letter_default.txt')
    app.request(raw,'/api/view',{'name':'profile'})
    session=app.session(raw)
    app.request(raw,'/api/action',{'id':action_id(session,'menu:switch')})
    state=app.request(raw,'/api/action',{'id':action_id(session,'switch:'+other)})
    assert state['profile']['name']=='Other'
    assert 'Профиль: Other' in state['menu']['title']
    assert state['section']=='profile'
