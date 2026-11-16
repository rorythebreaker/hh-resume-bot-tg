import datetime as dt
import json

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
    monkeypatch.setattr(discovery, "search_page", lambda *args: {
        "found": 150, "pages": 2, "items": [{"id": "1"}]})
    start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    path = tmp_path / "state.json"
    state = discovery.discover("main", {"text": "engineer"}, start,
                               start + dt.timedelta(days=1), path, max_requests=1)
    assert state["pending"]
    assert not state["completed"]
    assert json.loads(path.read_text())["pending"]
