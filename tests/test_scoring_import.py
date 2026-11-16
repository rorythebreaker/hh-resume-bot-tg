import pytest
import scoring
import scoring_config


def document():
    return {
        "dictionary_version": "1.0", "mode": "shadow",
        "search_integration": {"replace_search_query": False, "replace_existing_filters": False},
        "target": {"directions": ["sysadmin", "devops"], "seniority": ["junior", "middle"]},
        "weights": {"role": 25, "skills": 25, "duties": 25, "experience": 15, "work_format": 7, "salary": 3},
        "thresholds": {"high": 90, "manual_review": 50, "action_mode": "shadow"},
        "rules": {"role": ["системный администратор", "soc analyst"],
                  "skills": ["linux", "windows server", "wazuh", "zabbix"],
                  "duties": ["администрирован", "мониторинг"],
                  "negative": ["первая линия поддержки"]},
        "proposed_penalties": [
            {"id": "predominant_user_l1", "points": -40, "requires_context": True},
            {"id": "occasional_user_support", "points": -15, "requires_context": True},
            {"id": "sales_or_customer_service_primary", "points": -35, "requires_context": True},
            {"id": "senior_lead_5plus_required", "points": -20, "requires_context": True},
            {"id": "office_only", "points": -10, "requires_context": True},
        ],
        "test_cases": [{"vacancy": "example", "user": "positive"}],
    }


def test_import_full_dictionary_and_keep_shadow():
    data = scoring_config.normalize_import(document())
    assert data["weights"]["duties"] == 25
    assert data["penalties"]["l1_primary"] == 40
    assert data["dictionary_version"] == "1.0"
    assert "test_cases" not in data


def test_reject_unsafe_search_override():
    payload = document()
    payload["search_integration"]["replace_search_query"] = True
    with pytest.raises(ValueError):
        scoring_config.normalize_import(payload)


def test_reject_invalid_weights():
    payload = document()
    payload["weights"]["role"] = 40
    with pytest.raises(ValueError):
        scoring_config.normalize_import(payload)


def test_contextual_penalty_soc_vs_user_helpdesk():
    cfg = scoring_config.normalize_import(document())
    profile = {"scoring": cfg}
    soc = scoring.evaluate({"name": "SOC Analyst L1", "description": "Wazuh мониторинг событий"}, profile)
    helpdesk = scoring.evaluate({"name": "Специалист технической поддержки L1", "description": "Поддержка пользователей Windows Server"}, profile)
    assert soc["penalty_reasons"] == []
    assert any(p["reason"] == "l1_primary" for p in helpdesk["penalty_reasons"])
    assert soc["score"] > helpdesk["score"]


def test_legacy_negative_penalty_preserved():
    result = scoring.evaluate({"name": "Системный администратор", "description": "первая линия поддержки"}, {})
    assert result["penalty_reasons"][0]["points"] == 40
