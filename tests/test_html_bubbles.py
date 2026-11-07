import io
import json
import urllib.error
import zlib
from unittest.mock import Mock
import pytest
from html_bubbles import ASSET, HtmlBubbles

def fake_api():
    def call(method, **params):
        return {'ok':True,'result':{'message_id':77} if method=='sendWidget' else True}
    return Mock(side_effect=call)

def menu(count=4):
    return {'inline_keyboard':[[{'text':'Action '+str(i),'callback_data':'m:classic:cmd:/status'}] for i in range(count)]}

def sent(ui,api,buttons=4,title='<b>Main</b>'):
    assert ui.try_menu('123',title,menu(buttons),'profile_1')
    return ui.sessions[('123',77)]

def query(session,action='a0',qid='q1'):
    return {'id':qid,'from':{'id':123},'chat':{'id':123,'type':'private'},
            'message_id':77,'data':f'hb:{session.nonce}:{action}'}

def handle(ui,q,profile='profile_1'):
    dispatch,refresh=Mock(),Mock()
    ui.handle_query(q,'123',profile,dispatch,refresh)
    return dispatch,refresh

def test_static_asset_size_and_no_external_resources():
    data=ASSET.read_bytes()
    assert len(data)<=32768 and len(zlib.compress(data,9))<=4096
    text=data.decode()
    assert 'TelegramWidget' in text and 'chatDataChanged' in text
    assert 'fetch(' not in text and 'src=' not in text and '<input' not in text
    assert '.innerHTML' not in text

def test_send_widget_uses_private_state_only():
    api=fake_api();ui=HtmlBubbles(api);sent(ui,api)
    params=api.call_args.kwargs
    assert api.call_args.args[0]=='sendWidget' and params['allow_queries']=='true' and params['is_shared']=='false'
    assert 'public_data' not in params
    assert json.loads(params['chat_data'])['t']=='Main'
    assert 'profile_1' not in params['html']
    assert ui.wants_updates

def test_unsupported_endpoint_probes_only_once():
    api=Mock(side_effect=urllib.error.HTTPError('url',404,'not found',{},io.BytesIO(b'{}')))
    ui=HtmlBubbles(api)
    assert not ui.try_menu('123','Main',menu(),'p')
    assert not ui.try_menu('123','Main',menu(),'p')
    assert api.call_count==1 and not ui.wants_updates

def test_json_api_error_falls_back():
    api=Mock(return_value={'ok':False,'error_code':400,'description':'unknown method'})
    ui=HtmlBubbles(api)
    assert not ui.try_menu('123','Main',menu(),'p')

@pytest.mark.parametrize('code',[401,403,429,500])
def test_other_http_failures_never_duplicate_send(code):
    api=Mock(side_effect=urllib.error.HTTPError('url',code,'failed',{},io.BytesIO(b'{}')))
    assert HtmlBubbles(api).try_menu('123','Main',menu(),'p')
    assert api.call_count==1

def test_timeout_is_not_replayed_as_a_classic_message():
    api=Mock(side_effect=TimeoutError('timeout'))
    assert HtmlBubbles(api).try_menu('123','Main',menu(),'p')
    api.assert_called_once()

def test_off_has_no_network_probe():
    api=fake_api()
    assert not HtmlBubbles(api,'off').try_menu('123','Main',menu(),'p')
    api.assert_not_called()

def test_private_state_byte_budget_for_unicode():
    api=fake_api();ui=HtmlBubbles(api)
    buttons={'inline_keyboard':[[{'text':chr(128512)*44,'callback_data':'safe'}] for _ in range(5)]}
    assert ui.try_menu('123',chr(128512)*260,buttons,'p')
    assert len(api.call_args.kwargs['chat_data'].encode('utf-8'))<=1024

def test_long_text_preserves_classic_view():
    api=fake_api();ui=HtmlBubbles(api)
    assert not ui.try_menu('123','x'*261,menu(),'p')
    api.assert_not_called()

def test_edit_only_changes_state_not_html():
    api=fake_api();ui=HtmlBubbles(api);sent(ui,api)
    assert ui.try_menu('123','Next',menu(),'profile_1',77)
    assert api.call_args.args[0]=='editMessageWidgetData'
    assert 'html' not in api.call_args.kwargs

def test_classic_message_is_not_edited_as_widget():
    api=fake_api();ui=HtmlBubbles(api)
    assert not ui.try_menu('123','Next',menu(),'p',99)
    api.assert_not_called()

def test_query_uses_existing_callback():
    api=fake_api();ui=HtmlBubbles(api);session=sent(ui,api)
    dispatch,refresh=handle(ui,query(session))
    dispatch.assert_called_once_with('123',77,'m:classic:cmd:/status')
    refresh.assert_not_called()
    assert api.call_args.args[0]=='answerWidgetQuery'

def test_duplicate_update_never_dispatches_twice():
    api=fake_api();ui=HtmlBubbles(api);session=sent(ui,api)
    dispatch=Mock();refresh=Mock();q=query(session)
    ui.handle_query(q,'123','profile_1',dispatch,refresh)
    ui.handle_query(q,'123','profile_1',dispatch,refresh)
    dispatch.assert_called_once()

@pytest.mark.parametrize('field,value',[
 ('from',{'id':999}),('chat',{'id':999,'type':'private'}),
 ('chat',{'id':123,'type':'group'}),('message_id',99),
 ('inline_message_id','forwarded'),('data','cmd:/run'),('data','x'*65)])
def test_untrusted_or_forwarded_queries_are_rejected(field,value):
    api=fake_api();ui=HtmlBubbles(api);session=sent(ui,api)
    q=query(session);q[field]=value
    dispatch,refresh=handle(ui,q)
    dispatch.assert_not_called();refresh.assert_not_called()

def test_switched_profile_rejects_old_widget():
    api=fake_api();ui=HtmlBubbles(api);session=sent(ui,api)
    dispatch,_=handle(ui,query(session),profile='other')
    dispatch.assert_not_called()

def test_expired_and_restart_state_rejected():
    api=fake_api();now=[0];ui=HtmlBubbles(api,clock=lambda:now[0]);session=sent(ui,api)
    now[0]=1801
    dispatch,_=handle(ui,query(session))
    dispatch.assert_not_called()
    dispatch,_=handle(HtmlBubbles(api),query(session))
    dispatch.assert_not_called()

def test_pagination_cannot_invoke_hidden_buttons():
    api=fake_api();ui=HtmlBubbles(api);session=sent(ui,api,buttons=9)
    dispatch,_=handle(ui,query(session,'a7'));dispatch.assert_not_called()
    dispatch,_=handle(ui,query(session,'p1','q2'));dispatch.assert_not_called()
    state=json.loads(next(c.kwargs['chat_data'] for c in reversed(api.call_args_list) if c.args[0]=='editMessageWidgetData'))
    assert state['p']==1 and len(state['b'])==4
    dispatch,_=handle(ui,query(session,'a7','q3'));dispatch.assert_called_once()

def test_refresh_uses_bot_menu_builder():
    api=fake_api();ui=HtmlBubbles(api);session=sent(ui,api)
    dispatch,refresh=handle(ui,query(session,'r'))
    dispatch.assert_not_called();refresh.assert_called_once_with('123',77)

def test_bot_fallback_keeps_normal_inline_callbacks(workspace,monkeypatch):
    import tg_bot as bot
    def call(method,**kw):
        if method=='sendWidget': raise urllib.error.HTTPError('u',404,'no',{},io.BytesIO())
        return {'ok':True,'result':True}
    api=Mock(side_effect=call)
    monkeypatch.setattr(bot,'api_call',api)
    monkeypatch.setattr(bot,'HTML_BUBBLES',HtmlBubbles(lambda m,**kw:bot.api_call(m,**kw)))
    bot.send_menu('123',bot.build_menu_main())
    assert [c.args[0] for c in api.call_args_list]==['sendWidget','sendMessage']
    keyboard=json.loads(api.call_args.kwargs['reply_markup'])
    assert keyboard['inline_keyboard'][0][0]['callback_data'].startswith('m:')

def test_widget_to_long_help_sends_new_text_message(workspace,monkeypatch):
    import tg_bot as bot
    api=fake_api();ui=HtmlBubbles(api)
    monkeypatch.setattr(bot,'api_call',api);monkeypatch.setattr(bot,'HTML_BUBBLES',ui)
    bot.send_menu('123',bot.build_menu_main())
    bot.send_menu('123',{'title':'x'*300,'rows':[[('Back','menu:main')]]},edit_message_id=77)
    assert api.call_args.args[0]=='sendMessage' and 'message_id' not in api.call_args.kwargs

def test_classic_command_disables_widget_for_chat(workspace,monkeypatch):
    import tg_bot as bot
    api=fake_api();ui=HtmlBubbles(api)
    monkeypatch.setattr(bot,'api_call',api);monkeypatch.setattr(bot,'HTML_BUBBLES',ui)
    bot.handle_message('123','/classic',1)
    assert '123' in bot._CLASSIC_CHATS and api.call_args.args[0]=='sendMessage'

def test_widget_bulk_launch_still_requires_existing_confirmation(workspace,monkeypatch):
    import tg_bot as bot
    api=fake_api();ui=HtmlBubbles(api)
    monkeypatch.setattr(bot,'api_call',api);monkeypatch.setattr(bot,'HTML_BUBBLES',ui)
    monkeypatch.setattr(bot,'TG_CHAT','123')
    run=Mock(return_value='started');monkeypatch.setattr(bot,'_cmd_run_confirmed',run)
    bot.cmd_run('123')
    run.assert_not_called()
    session=ui.sessions[('123',77)]
    q=query(session)
    bot.handle_widget_query(q);bot.handle_widget_query(q)
    run.assert_called_once_with('123')

def test_diagnostic_distinguishes_method_not_found():
    api=Mock(side_effect=urllib.error.HTTPError('url',404,'not found',{},io.BytesIO(b'{"description":"Not Found"}')))
    ui=HtmlBubbles(api);ui.try_menu('123','Main',menu(),'p')
    assert 'HTTP 404' in ui.diagnostic_text()
    assert 'Not Found' in ui.diagnostic_text()

def test_diagnostic_does_not_mislabel_bad_parameters_as_missing_method():
    api=Mock(return_value={'ok':False,'error_code':400,'description':'INPUT_MEDIA_WIDGET_HTML_INVALID'})
    ui=HtmlBubbles(api);ui.try_menu('123','Main',menu(),'p')
    assert 'INPUT_MEDIA_WIDGET_HTML_INVALID' in ui.diagnostic_text()
    assert 'не доказывает' in ui.diagnostic_text()

def test_diagnostic_scrubs_bot_token(monkeypatch):
    monkeypatch.setenv('TG_BOT_TOKEN','TEST_SECRET_TOKEN')
    api=Mock(return_value={'ok':False,'error_code':400,'description':'request failed: TEST_SECRET_TOKEN'})
    ui=HtmlBubbles(api);ui.try_menu('123','Main',menu(),'p')
    assert 'TEST_SECRET_TOKEN' not in ui.diagnostic_text()

def test_diagnostic_success_confirms_actual_acceptance():
    api=fake_api();ui=HtmlBubbles(api);sent(ui,api)
    assert ui.last_result['kind']=='ok'
    assert 'подтверждена' in ui.diagnostic_text()

def test_diagnostic_unchecked_does_not_make_requests():
    api=fake_api();ui=HtmlBubbles(api)
    assert '/bubbles' in ui.diagnostic_text()
    api.assert_not_called()

def test_diagnostic_network_is_inconclusive():
    api=Mock(side_effect=TimeoutError())
    ui=HtmlBubbles(api);ui.try_menu('123','Main',menu(),'p')
    assert ui.last_result['kind']=='network'
    assert 'определить поддержку не удалось' in ui.diagnostic_text()

def test_ui_status_does_not_leak_endpoint_token_or_enter_auth_input(workspace,monkeypatch):
    import tg_bot as bot
    send=Mock();auth_input=Mock()
    monkeypatch.setattr(bot,'send',send)
    monkeypatch.setattr(bot,'handle_auth_input',auth_input)
    monkeypatch.setattr(bot,'API','https://api.telegram.org/botTEST_SECRET_TOKEN/test')
    monkeypatch.setitem(bot.AUTH_DIALOG,'active',True)
    bot.handle_message('123','/ui_status',1)
    output=send.call_args.args[1]
    assert 'api.telegram.org' in output and 'TEST_SECRET_TOKEN' not in output
    auth_input.assert_not_called()
