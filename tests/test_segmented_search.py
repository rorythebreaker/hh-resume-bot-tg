import datetime as dt
import json
import runpy
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import segmented_search as discovery


def test_segmented_search_splits_large_windows_and_deduplicates(tmp_path, monkeypatch):
    calls = []
    def fake(account, params, page):
        calls.append((params["date_from"], params["date_to"], page))
        a = dt.datetime.fromisoformat(params["date_from"])
        b = dt.datetime.fromisoformat(params["date_to"])
        if (b-a).total_seconds() > 3600:
            return {"found": 2200, "pages": 22, "items": []}
        return {"found": 1, "pages": 1, "items": [{"id": "42", "name": "Engineer"}]}
    monkeypatch.setattr(discovery, "search_page", fake)
    start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    end = start + dt.timedelta(hours=2)
    checkpoint = tmp_path / "state.json"
    state = discovery.discover("main", {"text": "engineer", "area": "113"}, start, end, checkpoint)
    assert not state["pending"]
    assert not state.get("overflow")
    assert len(state["vacancies"]) == 1
    assert len(state["completed"]) == 2
    resumed = discovery.discover("main", {"text": "engineer", "area": "113"}, start, end, checkpoint)
    assert resumed["vacancies"] == state["vacancies"]
    assert len(calls) == 3


def test_segmented_search_keeps_checkpoint_on_budget_exhaustion(tmp_path, monkeypatch):
    calls = []

    def search(account, params, page):
        calls.append((params["date_from"], params["date_to"], page))
        start, end = (0, 100) if page == 0 else (100, 150)
        return {"found": 150, "pages": 2, "items": [{"id": str(i)} for i in range(start, end)]}

    monkeypatch.setattr(discovery, "search_page", search)
    start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    end = start + dt.timedelta(days=1)
    path = tmp_path / "state.json"
    params = {"text": "engineer"}
    state = discovery.discover("main", params, start, end, path, max_requests=1)
    assert state["pending"]
    assert not state["completed"]
    assert json.loads(path.read_text())["page"]["next"] == 1
    shift = dt.timedelta(hours=2)
    state = discovery.discover("main", params, start + shift, end + shift, path, max_requests=1)
    assert calls == [(discovery.iso(start), discovery.iso(end), page) for page in (0, 1)]
    assert not state["pending"]
    assert len(state["vacancies"]) == 150
    assert state["requests"] == 2


def test_api_failure_preserves_the_next_page(tmp_path, monkeypatch):
    calls = []

    def search(account, params, page):
        calls.append(page)
        if calls == [0, 1]:
            raise discovery.hh_api.HHAPIError(429, "rate limited")
        items = [{"id": str(i)} for i in range(100)] if page == 0 else [{"id": "100"}]
        return {"found": 101, "pages": 2, "items": items}

    monkeypatch.setattr(discovery, "search_page", search)
    start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    end = start + dt.timedelta(days=1)
    path = tmp_path / "state.json"
    state = discovery.discover("main", {}, start, end, path)
    assert state["issue"]["status"] == 429
    assert state["page"]["next"] == 1
    state = discovery.discover("main", {}, start, end, path)
    assert calls == [0, 1, 1]
    assert state["issue"] is None
    assert not state["pending"] and len(state["vacancies"]) == 101


def test_inconsistent_page_resets_only_the_unfinished_segment(tmp_path, monkeypatch):
    start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    end = start + dt.timedelta(minutes=10)

    def search(account, params, page):
        left = dt.datetime.fromisoformat(params["date_from"])
        right = dt.datetime.fromisoformat(params["date_to"])
        if (right - left) == dt.timedelta(minutes=10):
            return {"found": 2000, "pages": 20, "items": []}
        if right == end:
            return {"found": 1, "pages": 1, "items": [{"id": "999"}]}
        items = [{"id": str(i)} for i in range(100)] if page == 0 else []
        return {"found": 150, "pages": 2, "items": items}

    monkeypatch.setattr(discovery, "search_page", search)
    state = discovery.discover("main", {}, start, end, tmp_path / "state.json")
    assert state["pending"] and len(state["completed"]) == 1
    assert state["issue"] == {"code": "inconsistent_page", "page": 1}
    assert state["page"] is None
    assert list(state["vacancies"]) == ["999"]


def test_saturated_five_minute_window_stays_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery, "search_page", lambda *args: {
        "found": 2000, "pages": 20, "items": [],
    })
    start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    end = start + dt.timedelta(minutes=5)
    state = discovery.discover("main", {}, start, end, tmp_path / "state.json")
    assert state["pending"] and not state["completed"]
    assert state["overflow"] == [[discovery.iso(start), discovery.iso(end), 2000]]
    assert state["requests"] == 1


def test_search_encodes_multiple_values_as_repeated_parameters(monkeypatch):
    def request(account, path):
        assert account == "main"
        return parse_qs(urlsplit(path).query)

    monkeypatch.setattr(discovery.hh_api, "request", request)
    result = discovery.search_page("main", {
        "work_format": ["REMOTE", "HYBRID"], "excluded_employer_id": ["42", "43"],
    }, 3)
    assert result["work_format"] == ["REMOTE", "HYBRID"]
    assert result["excluded_employer_id"] == ["42", "43"]
    assert result["page"] == ["3"] and result["per_page"] == ["100"]


def test_runner_dry_run_waits_for_complete_manifest(tmp_path, monkeypatch, capsys):
    import profiles_lib

    root = Path(__file__).resolve().parents[1]
    main = runpy.run_path(str(root / "run-apply.py"))["main"]
    profile = {
        "search": "engineer", "area": "113",
        "filters": {
            "period": 2, "excluded_employer_ids": ["42"],
            "order_by": "publication_time", "segmented_search": True,
        },
    }
    monkeypatch.setattr(profiles_lib, "get_active_profile", lambda: ("test", profile))
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("APP_DIR", str(root))
    monkeypatch.setenv("APPLY_SEGMENTED", "0")
    monkeypatch.setenv("APPLY_DISCOVERY_DAYS", "7")
    monkeypatch.setenv("APPLY_DISCOVERY_REQUESTS", "1")
    monkeypatch.setenv("APPLY_DRY_RUN", "1")
    monkeypatch.setenv("APPLY_IGNORE_PAUSE", "1")
    monkeypatch.setenv("APPLY_SCHEDULED", "0")
    monkeypatch.setenv("HH_BOT_VACANCY_MANIFEST", "")
    monkeypatch.setenv("HH_BOT_OVERLAY", "{}")
    monkeypatch.setenv("HH_BOT_CAPTCHA_UI", "0")

    def search(account, params, page):
        assert params["excluded_employer_id"] == ["42"]
        assert params["order_by"] == "publication_time"
        assert (dt.datetime.fromisoformat(params["date_to"]) - dt.datetime.fromisoformat(params["date_from"])).days == 2
        assert "period" not in params
        items = [{"id": str(i)} for i in range(100)] if page == 0 else [{"id": "100"}]
        return {"found": 101, "pages": 2, "items": items}

    monkeypatch.setattr(discovery, "search_page", search)
    runs = []

    def run_pass(args):
        import os
        manifest = Path(os.environ["HH_BOT_VACANCY_MANIFEST"])
        runs.append((args, manifest, json.loads(manifest.read_text())))
        return 0, False, False, 0

    monkeypatch.setitem(main.__globals__, "run_pass", run_pass)
    assert main() == 2 and not runs
    assert "STOP_REASON: discovery_incomplete" in capsys.readouterr().out
    assert main() == 0
    assert "STOP_REASON: dry_run" in capsys.readouterr().out
    args, manifest, vacancies = runs[0]
    assert "--dry-run" in args and len(vacancies) == 101
    assert manifest.name == "test.dry-run.vacancies.json"
    assert not (manifest.parent / "test.dry-run.json").exists()
