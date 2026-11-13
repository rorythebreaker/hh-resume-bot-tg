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
    assert "if method.upper() == 'POST':" in source
    assert "return _post_with_captcha(account, path, fields)" in source


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
