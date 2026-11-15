"""Regression tests for application-run limits and manual CAPTCHA routing."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_manual_post_uses_captcha_aware_client():
    source = (ROOT / "hh_api.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert "_post_with_captcha" in functions
    assert "client.post(path, fields or {})" in source
    assert "client.max_retries = 1" in source
    assert "if method.upper() == 'POST' or (captcha_ui and method.upper() == 'GET'):" in source
    assert "return _post_with_captcha(account, path, fields, method.upper())" in source


def test_run_respects_configured_response_cap():
    source = (ROOT / "run-apply.py").read_text(encoding="utf-8")
    assert "total_sent >= max_responses" in source
    assert 'stop_reason = "max_responses"' in source
    assert 'stop_reason = "scan_completed"' in source
    assert 'args.extend(["--total-pages", str(pages)])' in source


def test_no_double_counting():
    source = (ROOT / "run-apply.py").read_text(encoding="utf-8")
    assert "post_count += 1" in source
    assert "return (sent if core_seen else post_count)" in source


def test_captcha_success_event():
    source = (ROOT / "hh_cli.py").read_text(encoding="utf-8")
    assert "pending = getattr(self,'_captcha_pending_api',False)" in source
    assert "if pending:" in source


def test_relation_skip_preserves_upstream_decision(monkeypatch):
    from hh_cli import ObservedApplyOperation
    from hh_applicant_tool.operations.apply_vacancies import Operation
    monkeypatch.setattr(Operation, '_should_skip_vacancy', lambda self, vacancy: True)
    operation = object.__new__(ObservedApplyOperation)
    operation.metrics = {'skip_reasons': {}}
    vacancy = {'id': '123', 'relations': ['got_response']}
    assert operation._should_skip_vacancy(vacancy) is True
    assert operation.metrics['skip_reasons'] == {'relations': 1}


def test_relation_skip_never_overrides_upstream_allow(monkeypatch):
    from hh_cli import ObservedApplyOperation
    from hh_applicant_tool.operations.apply_vacancies import Operation
    monkeypatch.setattr(Operation, '_should_skip_vacancy', lambda self, vacancy: False)
    operation = object.__new__(ObservedApplyOperation)
    operation.metrics = {'skip_reasons': {}}
    assert operation._should_skip_vacancy({'id': '123', 'relations': []}) is False
    assert operation.metrics['skip_reasons'] == {}


def test_captcha_terminal_status_notifies_owner(tmp_path, monkeypatch):
    import apply_captcha
    monkeypatch.setenv('CONFIG_DIR', str(tmp_path))
    notified = []
    monkeypatch.setattr(apply_captcha, 'notify_result', notified.append)
    apply_captcha.event('profile', 'finished', 'Проверка завершена без подтверждения.')
    assert notified == ['Проверка завершена без подтверждения.']
    assert apply_captcha.status('profile')['state'] == 'finished'


def test_captcha_waiting_does_not_send_result(tmp_path, monkeypatch):
    import apply_captcha
    monkeypatch.setenv('CONFIG_DIR', str(tmp_path))
    notified = []
    monkeypatch.setattr(apply_captcha, 'notify_result', notified.append)
    apply_captcha.event('profile', 'waiting', 'Введите CAPTCHA.')
    assert notified == []


def test_manual_get_uses_captcha_aware_client():
    source = (ROOT / 'manual_flow.py').read_text(encoding='utf-8')
    assert "hh_api.request(profile['hh_account'], '/me', captcha_ui=True)" in source
    assert "hh_api.request(profile['hh_account'], f'/vacancies/{vid}', captcha_ui=True)" in source
    api_source = (ROOT / 'hh_api.py').read_text(encoding='utf-8')
    assert "result = client.get(path)" in api_source
    assert "captcha_ui and method.upper() == 'GET'" in api_source


def test_manual_confirmation_rechecks_vacancy_with_captcha_client():
    source = (ROOT / 'manual_flow.py').read_text(encoding='utf-8')
    assert source.count("hh_api.request(profile['hh_account'], f'/vacancies/{vid}', captcha_ui=True)") == 2
    assert "bot.hh_get_strict(f'/vacancies/{vid}')" not in source


def test_manual_summary_tracks_unprocessed_items_independently_of_errors():
    source = (ROOT / 'manual_flow.py').read_text(encoding='utf-8')
    assert 'remaining = max(0, len(vacancies) - processed)' in source
    assert "summary += ' Причина остановки: '" in source


def test_pre_filter_skips_known_ineligible_before_description_request(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from hh_cli import ObservedApplyOperation
    from hh_applicant_tool.operations.apply_vacancies import Operation

    operation = object.__new__(ObservedApplyOperation)
    operation.metrics = {'sent': 0, 'skipped': 0, 'skip_reasons': {}}
    client = Mock()
    monkeypatch.setattr(ObservedApplyOperation, 'api_client', property(lambda self: client))
    monkeypatch.setattr(Operation, '_should_skip_vacancy',
                        lambda self, vacancy: bool(vacancy.get('relations')))
    state = SimpleNamespace(do_apply=True, applied_count=0)
    operation._process_vacancy(
        {'id': '123', 'alternate_url': 'https://hh.ru/vacancy/123',
         'relations': ['got_rejection']},
        {}, {}, set(), state)
    client.get.assert_not_called()
    assert operation.metrics['skipped'] == 1
    assert operation.metrics['pre_skipped'] == 1
    assert operation.metrics['skip_reasons'] == {'relations': 1}


def test_pre_filter_allows_eligible_vacancy_into_upstream(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    import profiles_lib
    from hh_cli import ObservedApplyOperation
    from hh_applicant_tool.operations.apply_vacancies import Operation

    operation = object.__new__(ObservedApplyOperation)
    operation.metrics = {'sent': 0, 'skipped': 0, 'errors': 0, 'skip_reasons': {}}
    client = Mock()
    monkeypatch.setattr(ObservedApplyOperation, 'api_client', property(lambda self: client))
    monkeypatch.setattr(profiles_lib, 'get_active_profile', lambda: ('p', None))
    monkeypatch.setattr(Operation, '_should_skip_vacancy',
                        lambda self, vacancy: False)
    processed = []
    monkeypatch.setattr(Operation, '_process_vacancy',
                        lambda self, vacancy, *args: processed.append(vacancy['id']))
    state = SimpleNamespace(do_apply=True, applied_count=0)
    operation._process_vacancy(
        {'id': '124', 'alternate_url': 'https://hh.ru/vacancy/124',
         'relations': []},
        {}, {}, set(), state)
    assert processed == ['124']
    assert operation.metrics.get('pre_skipped', 0) == 0
