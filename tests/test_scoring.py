"""Shadow-scoring regression tests; no live HH or Telegram required."""
import sqlite3
from types import SimpleNamespace

import pytest

import scoring


def test_rule_scoring_is_explainable_and_bounded():
    good = scoring.evaluate({"id": "1", "name": "DevOps engineer",
        "description": "Linux Docker Ansible monitoring automation", "experience": {"id": "between1And3"}})
    bad = scoring.evaluate({"id": "2", "name": "L1 support",
        "description": "call-центр, первая линия поддержки"})
    assert 0 <= bad["score"] < good["score"] <= 100
    assert "docker" in good["matched_skills"]
    assert good["mode"] == "shadow"
    assert good["parts"]["role"] == 30


def test_custom_rules_are_profile_scoped():
    vacancy = {"id": "3", "name": "Инженер сетей", "description": "MikroTik"}
    default = scoring.evaluate(vacancy)
    custom = scoring.evaluate(vacancy, {"scoring": {"rules": {
        "role": ["инженер сетей"], "skills": ["mikrotik"]}}})
    assert custom["score"] > default["score"]


def test_sqlite_upsert_and_feedback_are_separate(tmp_path):
    path = tmp_path / "scoring.db"
    vacancy = {"id": "123", "name": "DevOps", "description": "Docker"}
    score = scoring.evaluate(vacancy)
    scoring.save_evaluation("profile_1", vacancy, score, path)
    scoring.add_feedback("profile_1", "123", "positive", "good_stack", path)
    scoring.save_evaluation("profile_1", vacancy, score, path)
    scoring.save_evaluation("profile_2", vacancy, score, path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 1
        assert db.execute("SELECT decision FROM feedback").fetchone()[0] == "positive"


def test_invalid_feedback_rejected(tmp_path):
    with pytest.raises(ValueError):
        scoring.add_feedback("p", "123", "got_rejection", path=tmp_path / "db.sqlite")


def test_shadow_scoring_failure_does_not_stop_upstream(monkeypatch):
    from hh_cli import ObservedApplyOperation
    from hh_applicant_tool.operations.apply_vacancies import Operation
    import profiles_lib

    operation = object.__new__(ObservedApplyOperation)
    operation.metrics = {"sent": 0, "skipped": 0, "errors": 0, "skip_reasons": {}}
    monkeypatch.setattr(Operation, "_should_skip_vacancy", lambda *args: False)
    monkeypatch.setattr(profiles_lib, "get_active_profile", lambda: ("p", {"hh_account": "main"}))
    monkeypatch.setattr(scoring, "save_evaluation", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    seen = []
    monkeypatch.setattr(Operation, "_process_vacancy", lambda self, v, *args: seen.append(v["id"]))
    state = SimpleNamespace(do_apply=True, applied_count=0)
    operation._process_vacancy({"id": "123", "name": "DevOps", "alternate_url": "https://hh.ru/vacancy/123"}, {}, {}, set(), state)
    assert seen == ["123"]
    assert operation.metrics["scoring_errors"] == 1
