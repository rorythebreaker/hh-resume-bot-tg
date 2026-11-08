import ast
import base64
import importlib.util
import json
import logging
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import urllib.error
import pytest
import profiles_lib
import hh_compat
import hh_api
import manual_flow
from security import redact, SecretFilter

ROOT = Path(__file__).resolve().parents[1]

def load_runner():
    spec = importlib.util.spec_from_file_location('run_apply', ROOT/'run-apply.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

def test_all_sources_compile():
    for file in ROOT.glob('*.py'): ast.parse(file.read_text('utf-8'))

@pytest.mark.parametrize('account', ['../x', '', 'a/b', '/tmp', '..', 'a b'])
def test_account_traversal_rejected(account):
    with pytest.raises(ValueError): hh_compat.validate_account(account)

def test_accounts_never_share_root_token(tmp_path):
    (tmp_path/'token.json').write_text(json.dumps({'token': {'access_token':'ROOT'}}))
    assert hh_compat.read_access_token(tmp_path, 'other') is None
    assert hh_compat.read_access_token(tmp_path, 'main') == 'ROOT'

def test_account_config_has_priority(tmp_path):
    (tmp_path/'config.toml').write_text('[general]\nname="root"')
    (tmp_path/'other').mkdir()
    (tmp_path/'other'/'config.toml').write_text('[general]\nname="other"')
    assert hh_compat.read_profile_config(tmp_path, 'other')['general']['name'] == 'other'

def test_atomic_profiles_and_pinned_context(workspace):
    root, pid = workspace
    other = profiles_lib.add_profile('Other', 'other', 'sre', 'letter_default.txt')
    _, snapshot = profiles_lib.get_active_profile()
    with profiles_lib.profile_context(pid, snapshot):
        profiles_lib.set_active_profile(other)
        assert profiles_lib.get_active_profile()[0] == pid
    assert profiles_lib.get_active_profile()[0] == other
    assert not list(root.glob('.*.tmp'))

def test_letter_migration_is_idempotent(workspace):
    root, pid = workspace
    other = profiles_lib.add_profile('Other', 'main', 'x', 'letter_default.txt')
    profiles_lib.migrate_unique_letters()
    data = profiles_lib.load_profiles()
    first = profiles_lib.get_letter_path(data['profiles'][pid])
    second = profiles_lib.get_letter_path(data['profiles'][other])
    assert first != second
    first.write_text('edited', encoding='utf-8')
    profiles_lib.migrate_unique_letters()
    assert first.read_text() == 'edited'
    assert second.read_text().startswith('Hello')

def test_corrupt_profiles_fail_without_overwrite(workspace):
    root, _ = workspace
    (root/'profiles.json').write_text('{broken')
    with pytest.raises(ValueError): profiles_lib.load_profiles()
    assert (root/'profiles.json').read_text() == '{broken'

@pytest.mark.parametrize('status,part', [(400,'параметры'),(401,'токен'),(403,'CAPTCHA'),(404,'не найдена'),(429,'частота'),(500,'временная'),(503,'временная')])
def test_error_categories(status, part):
    assert part in hh_api.error_message(status)
    if status != 404: assert 'не найдена' not in hh_api.error_message(status)


def response(data):
    r = Mock(); r.__enter__ = Mock(return_value=r); r.__exit__ = Mock(return_value=False)
    r.read.return_value = json.dumps(data).encode()
    return r

def test_get_retries_are_bounded(monkeypatch):
    monkeypatch.setattr(hh_api, 'read_access_token', lambda *a:'TOKEN')
    monkeypatch.setattr(hh_api.time,'sleep',lambda *a:None)
    call = Mock(side_effect=urllib.error.HTTPError('https://api.hh.ru/me',503,'bad',{},io.BytesIO(b'{}')))
    monkeypatch.setattr(hh_api.urllib.request,'urlopen',call)
    with pytest.raises(hh_api.HHAPIError) as err: hh_api.request('main','/me')
    assert err.value.status == 503 and call.call_count == 3

def test_post_never_retries_ambiguous_result(monkeypatch):
    monkeypatch.setattr(hh_api,'read_access_token',lambda *a:'TOKEN')
    call = Mock(side_effect=urllib.error.URLError('lost'))
    monkeypatch.setattr(hh_api.urllib.request,'urlopen',call)
    with pytest.raises(hh_api.HHAPIError): hh_api.request('main','/negotiations','POST',{'vacancy_id':'1'})
    assert call.call_count == 1

def test_refresh_once_after_401(monkeypatch):
    monkeypatch.setattr(hh_api,'read_access_token',lambda *a:'TOKEN')
    monkeypatch.setattr(hh_api.subprocess,'run',Mock(return_value=SimpleNamespace(returncode=0)))
    call = Mock(side_effect=[urllib.error.HTTPError('x',401,'bad',{},io.BytesIO(b'{}')),response({'id':'1'})])
    monkeypatch.setattr(hh_api.urllib.request,'urlopen',call)
    assert hh_api.request('main','/me')['id']=='1'
    assert hh_api.subprocess.run.call_count==1

def test_runner_uses_real_254_parser_and_overlay(workspace, monkeypatch):
    from hh_applicant_tool.tool import HHApplicantTool
    root, pid = workspace
    profiles_lib.update_filters(pid, salary=100000, work_format=['REMOTE'], max_responses=7, excluded_employer_ids=['123'])
    profiles_lib.update_profile(pid, excluded_filter='sales,helpdesk')
    module = load_runner(); monkeypatch.setattr(module,'LETTERS_DIR',str(root/'letters'))
    calls=[]
    monkeypatch.setattr(module,'run_pass',lambda args: (calls.append(args) or (2,False,False,0)))
    assert module.main()==0
    args=calls[0]; parsed=HHApplicantTool()._parser.parse_args(args[2:])
    assert parsed.max_responses==7 and parsed.salary==100000 and parsed.work_format==['REMOTE']
    assert parsed.excluded_filter=='sales|helpdesk'
    assert json.loads(__import__('os').environ['HH_BOT_OVERLAY'])['general.cover_letter'].startswith('Hello')
    assert len(calls)==1

def test_upstream_config_overlay_is_not_persisted(tmp_path,monkeypatch):
    from hh_cli import BotTool
    from hh_applicant_tool.tool import BaseNamespace
    monkeypatch.setenv('HH_BOT_OVERLAY',json.dumps({'general.cover_letter':'unique letter'}))
    tool=BotTool(); args=tool._parser.parse_args(['--config-dir',str(tmp_path),'--profile','main','whoami'],namespace=BaseNamespace()); tool._assign_args(args)
    assert tool.general_config['cover_letter']=='unique letter'
    assert not (tmp_path/'main'/'config.toml').exists()

@pytest.mark.parametrize('value',['Bearer abcSECRET','api_key=abcSECRET','https://u:abcSECRET@host','https://api.telegram.org/botabcSECRET/sendMessage'])
def test_redaction(value):
    assert 'abcSECRET' not in redact(value)

def test_upstream_log_normalization():
    f=SecretFilter()
    for message in ["(1/5) POST https://api.hh.ru/negotiations data={'vacancy_id': '12345'}",'POST https://api.hh.ru/negotiations 201']:
        record=logging.LogRecord('test',10,'',1,message,(),None); f.filter(record)
    assert record.msg=="201 POST https://api.hh.ru/negotiations with params: {'vacancy_id': '12345'}"

def test_migration_preserves_source_and_updates_ai_schema(tmp_path):
    from migrate_config import migrate
    old={'token':{'access_token':'TOKEN','refresh_token':'REFRESH'},'openai_cover_letter':{'api_key':'KEY','base_url':'https://example.org/v1/chat/completions'}}
    source=tmp_path/'config.json'; source.write_text(json.dumps(old))
    assert migrate(tmp_path).startswith('would create')
    assert not (tmp_path/'token.json').exists()
    assert migrate(tmp_path,True).startswith('written')
    import tomllib
    cfg=tomllib.loads((tmp_path/'config.toml').read_text())
    assert cfg['openai']['cover_letter']['base_url']=='https://example.org/v1'
    assert json.loads(source.read_text())==old
    assert migrate(tmp_path,True).startswith('skip: existing')

def test_captcha_partial_and_complete(monkeypatch,tmp_path):
    import auth_helper
    monkeypatch.setattr(auth_helper,'CAPTCHA_FILE',tmp_path/'captcha.png')
    png=b'\x89PNG\r\n\x1a\n' + b'data'
    packet=b'\x1b_Ga=T,f=100;'+base64.b64encode(png)+b'\x1b\\'
    assert auth_helper.strip_captcha(packet[:-2])[0]=='incomplete'
    status,clean=auth_helper.strip_captcha(packet)
    assert status=='done' and b'captcha image' in clean
    assert (tmp_path/'captcha.png').read_bytes()==png

@pytest.mark.parametrize('text,expected', [('https://hh.ru/vacancy/139159350',['139159350']),('https://spb.hh.ru/vacancy/139159350?x=1',['139159350']),('139159350 139159350',['139159350']),('hello',[])])
def test_manual_ids(text,expected):
    assert manual_flow.parse_ids(text)==expected

def test_stale_manual_confirmation_never_posts():
    bot=SimpleNamespace(send=Mock(),hh_post_negotiation=Mock())
    manual_flow.confirm(bot,'1','manual:expired:plain')
    bot.send.assert_called_once(); bot.hh_post_negotiation.assert_not_called()

def test_preview_never_posts_and_confirmation_is_single_use(workspace,monkeypatch):
    import tg_bot as bot
    root,pid=workspace
    monkeypatch.setattr(bot,'CONFIG_DIR',root/'config'); monkeypatch.setattr(bot,'PAUSE_FLAG',root/'config'/'.paused')
    monkeypatch.setattr(bot,'send',Mock()); monkeypatch.setattr(bot,'send_menu',Mock())
    monkeypatch.setattr(bot,'hh_get_strict',Mock(side_effect=lambda path: {'id':'123456','name':'Developer'} if '/vacancies/' in path else {}))
    monkeypatch.setattr(bot,'parse_log_applies',lambda:[])
    monkeypatch.setattr(bot,'hh_post_negotiation',Mock(return_value=(True,'')))
    monkeypatch.setattr(bot,'append_tool_log',Mock())
    class Immediate:
        def __init__(self,target,**kw): self.target=target
        def start(self): self.target()
    monkeypatch.setattr(manual_flow.threading,'Thread',Immediate)
    manual_flow.prepare(bot,'1','https://hh.ru/vacancy/123456')
    bot.hh_post_negotiation.assert_not_called()
    menu=bot.send_menu.call_args.args[1]; callback=menu['rows'][0][0][1]
    other=profiles_lib.add_profile('Other','other','sre','letter_default.txt',resume_id='otherResume')
    profiles_lib.set_active_profile(other)
    manual_flow.confirm(bot,'1',callback); manual_flow.confirm(bot,'1',callback)
    bot.hh_post_negotiation.assert_called_once()
    assert bot.hh_post_negotiation.call_args.args[1]=='resume123'

def test_watcher_rotated_file_and_persisted_position(tmp_path,monkeypatch):
    import tg_watcher as w
    monkeypatch.setattr(w,'STATE_FILE',tmp_path/'state.json')
    monkeypatch.setattr(w,'notify_apply',Mock()); monkeypatch.setattr(w,'reset_auth_alert',Mock())
    log=tmp_path/'log.txt'; log.write_text('old\n'); state={}
    w.process_log('main',log,state)
    with log.open('a') as f: f.write("201 POST https://api.hh.ru/negotiations with params: {'vacancy_id': '12345'}\n")
    w.process_log('main',log,state)
    state=json.loads((tmp_path/'state.json').read_text())
    w.process_log('main',log,state)
    w.notify_apply.assert_called_once_with('12345','main')

def test_wrong_hh_error_never_announced_as_expired_token(monkeypatch):
    import tg_watcher as w
    monkeypatch.setattr(w,'hh_api_get',lambda *a:(None,'http_429'))
    monkeypatch.setattr(w,'notify_auth_required',Mock())
    w.check_account_health('main',{})
    w.notify_auth_required.assert_not_called()

def test_mass_confirmation_is_required(workspace,monkeypatch):
    import tg_bot as bot
    monkeypatch.setattr(bot,'send_menu',Mock()); monkeypatch.setattr(bot,'_cmd_run_confirmed',Mock(return_value='started')); monkeypatch.setattr(bot,'send',Mock())
    bot.cmd_run('1'); bot._cmd_run_confirmed.assert_not_called()
    cb=bot.send_menu.call_args.args[1]['rows'][0][0][1]
    bot.handle_callback('1',10,cb); bot.handle_callback('1',10,cb)
    bot._cmd_run_confirmed.assert_called_once()

def test_schedule_persistence_and_validation(workspace):
    from schedule_config import save_hours, read_hours
    assert save_hours('8-10,18') == [8,9,10,18]
    assert read_hours() == [8,9,10,18]
    with pytest.raises(ValueError): save_hours('24')

def test_run_history_survives_reload(workspace):
    from run_history import append, load
    append({'profile':'p','sent':2})
    assert load()[-1]['sent'] == 2

def test_pause_blocks_automatic_pass(workspace,monkeypatch):
    root,_ = workspace
    module=load_runner()
    monkeypatch.setattr(module,'LETTERS_DIR',str(root/'letters'))
    monkeypatch.setattr(module,'PAUSE_FLAG',str(root/'config'/'.paused'))
    (root/'config'/'.paused').touch()
    monkeypatch.setattr(module,'run_pass',Mock())
    assert module.main()==0
    module.run_pass.assert_not_called()

def test_upstream_automatic_application_with_fake_api(tmp_path, monkeypatch):
    from hh_cli import BotTool
    from hh_applicant_tool.tool import BaseNamespace
    from hh_applicant_tool.operations.apply_vacancies import Operation
    tool=BotTool()
    args=tool._parser.parse_args(['--config-dir',str(tmp_path),'--profile','main','--no-use-ai','apply-vacancies','--search','python','--max-responses','1'],namespace=BaseNamespace())
    tool._assign_args(args)
    tool.__dict__['config']={'general': {'cover_letter':'Hello %(vacancy_name)s'}}
    resume={'id':'resume','status':{'id':'published'},'alternate_url':'https://hh.ru/resume/resume','title':'Engineer'}
    vacancies=[{'id':'12345','name':'Python','alternate_url':'https://hh.ru/vacancy/12345','employer':{'id':'1','name':'Test'},'relations':[]}, {'id':'12346','name':'Other','alternate_url':'https://hh.ru/vacancy/12346','employer':{'id':'2'},'relations':[]}]
    api=SimpleNamespace(post=Mock(return_value={}), get=Mock(return_value={}))
    tool.__dict__['api_client']=api
    monkeypatch.setattr(tool,'get_resumes',lambda:[resume])
    monkeypatch.setattr(tool,'get_me',lambda:{})
    monkeypatch.setattr(tool,'get_resume_placeholders',lambda rid:{})
    monkeypatch.setattr(Operation,'_get_vacancies',lambda self,**kwargs:iter(vacancies))
    from hh_cli import ObservedApplyOperation
    operation = ObservedApplyOperation()
    operation.run(tool,args)
    assert operation.metrics['sent'] == 1
    assert operation.metrics['errors'] == 0
    api.post.assert_called_once()
    assert api.post.call_args.args[1]['message']=='Hello Python'
    assert api.post.call_args.args[1]['resume_id']=='resume'

def test_ai_transport_settings_do_not_leak_into_payload(tmp_path):
    from hh_cli import BotTool
    tool=BotTool()
    tool.__dict__['get_openai_config'] = lambda section: {'base_url':'http://localhost:1234/v1','model':'test','max_retries':2,'proxy_url':'http://localhost:8080'}
    chat=tool.ai_chat_for('openai.cover_letter',system_prompt='Use resume facts only')
    assert chat.client.max_retries==2
    assert 'max_retries' not in chat.extra and 'proxy_url' not in chat.extra

def test_menu_nonce_rejects_old_profile(workspace,monkeypatch):
    import tg_bot as bot
    root,pid=workspace
    monkeypatch.setattr(bot,'api_call',Mock())
    monkeypatch.setattr(bot,'send',Mock())
    monkeypatch.setattr(bot,'run_command',Mock())
    bot.send_menu('1',{'title':'Test','rows':[[('Status','cmd:/status')]]})
    keyboard=json.loads(bot.api_call.call_args.kwargs['reply_markup'])
    callback=keyboard['inline_keyboard'][0][0]['callback_data']
    other=profiles_lib.add_profile('Other','other','x','letter_default.txt')
    profiles_lib.set_active_profile(other)
    bot.handle_callback('1',1,callback)
    bot.run_command.assert_not_called()

def test_observed_upstream_skip_counter(monkeypatch):
    from hh_cli import ObservedApplyOperation
    from hh_applicant_tool.operations.apply_vacancies import Operation
    operation=ObservedApplyOperation()
    operation.metrics={'found':0,'sent':0,'skipped':0,'errors':0}
    state=SimpleNamespace(applied_count=0)
    monkeypatch.setattr(Operation,'_process_vacancy',lambda *args:None)
    operation._process_vacancy({}, {}, {}, set(), state)
    assert operation.metrics['skipped']==1 and operation.metrics['sent']==0


def test_plain_forbidden_does_not_abort_before_hh_error_body(monkeypatch):
    module=load_runner()
    from io import StringIO
    proc=Mock()
    proc.stdout=StringIO('403 POST https://api.hh.ru/negotiations\n[E] negotiations: already_applied\n')
    proc.wait.return_value=0
    monkeypatch.setattr(module.subprocess,'Popen',Mock(return_value=proc))
    sent,limit,blocked,rc=module.run_pass(['fake'])
    assert not blocked
    assert module.METRICS['errors']==1
    proc.terminate.assert_not_called()


def test_rate_limit_still_stops_run(workspace,monkeypatch):
    module=load_runner()
    from io import StringIO
    root,_=workspace
    monkeypatch.setattr(module,'PAUSE_FLAG',str(root/'config'/'.paused'))
    proc=Mock();proc.stdout=StringIO('429 POST https://api.hh.ru/negotiations\n')
    proc.wait.return_value=2
    monkeypatch.setattr(module.subprocess,'Popen',Mock(return_value=proc))
    assert module.run_pass(['fake'])[2]
    assert (root/'config'/'.paused').exists()
    proc.terminate.assert_called_once()


@pytest.mark.parametrize('code,location,accepted',[
    (302,'hh://applicant/captchaSuccessBackUrl',True),
    (302,'/account/captcha?state=rejected',False),
    (403,'',False),(200,'',False)])
def test_captcha_validates_actual_hh_redirect(workspace,monkeypatch,code,location,accepted):
    from hh_cli import BotTool
    import apply_captcha
    monkeypatch.setenv('HH_BOT_CAPTCHA_UI','1')
    session=Mock()
    session.cookies.get.return_value='xsrf-private'
    session.request.return_value=Mock(status_code=code,headers={'Location':location})
    result=BotTool()._send_captcha('private-answer','private-key','private-state',
        referer_url='https://hh.ru/account/captcha',
        backurl='hh://applicant/captchaSuccessBackUrl',session=session)
    assert result is accepted
    message=apply_captcha.status(workspace[1])
    assert message['state']==('accepted' if accepted else 'rejected')
    assert 'private' not in message['message']
    sent=session.request.call_args.kwargs
    assert sent['params']['captchaText']=='private-answer'
    assert sent['allow_redirects'] is False


def test_captcha_cycle_is_bounded(workspace,monkeypatch):
    from hh_cli import BotTool
    import apply_captcha
    monkeypatch.setenv('HH_BOT_CAPTCHA_UI','1')
    request=Mock(return_value='answer')
    monkeypatch.setattr(apply_captcha,'request',request)
    tool=BotTool()
    for _ in range(3): assert tool._solve_captcha(b'image')=='answer'
    with pytest.raises(SystemExit): tool._solve_captcha(b'image')
    assert request.call_count==3
    assert apply_captcha.status(workspace[1])['state']=='stopped'


def test_captcha_timeout_becomes_controlled_stop(workspace,monkeypatch):
    from hh_cli import BotTool
    import apply_captcha
    monkeypatch.setenv('HH_BOT_CAPTCHA_UI','1')
    monkeypatch.setattr(apply_captcha,'request',Mock(side_effect=TimeoutError('expired')))
    with pytest.raises(SystemExit) as error: BotTool()._solve_captcha(b'image')
    assert error.value.code==4


def test_captcha_notification_has_no_challenge_secrets(monkeypatch):
    import apply_captcha,json
    from unittest.mock import MagicMock
    monkeypatch.setenv('TG_BOT_TOKEN','test-not-a-real-token')
    monkeypatch.setenv('TG_CHAT_ID','123')
    monkeypatch.setenv('TG_MINI_APP_URL','https://miniapp.example.com:8443')
    transport=MagicMock()
    monkeypatch.setattr('urllib.request.urlopen',transport)
    apply_captcha.notify_owner()
    request=transport.call_args.args[0]
    data=json.loads(request.data)
    assert data['chat_id']=='123'
    assert data['reply_markup']['inline_keyboard'][0][0]['web_app']['url']=='https://miniapp.example.com:8443'
    assert 'captchaText' not in request.data.decode()
