"""Resumable HH discovery using bounded publication-time windows."""
import datetime as dt
import json
from pathlib import Path
from urllib.parse import urlencode

import hh_api
from storage import atomic_write

UTC = dt.timezone.utc
PAGE_SIZE = 100
MAX_PAGES = 20
SPLIT_AT = 1900
DATE_RESOLUTION = dt.timedelta(minutes=5)
CHECKPOINT_VERSION = 2


def iso(value):
    return value.strftime("%Y-%m-%dT%H:%M:%S%z")


def search_page(account, params, page):
    query = urlencode({**params, "page": page, "per_page": PAGE_SIZE}, doseq=True)
    return hh_api.request(account, "vacancies?" + query)


def vacancy_search_params(text, area, filters):
    params = {"text": text, "area": area}
    for key in ("experience", "salary", "order_by"):
        if filters[key]:
            params[key] = filters[key]
    if filters["only_with_salary"]:
        params["only_with_salary"] = "true"
    if filters["excluded_employer_ids"]:
        params["excluded_employer_id"] = filters["excluded_employer_ids"]
    if not filters["geo_enabled"]:
        for key in ("schedule", "work_format"):
            if filters[key]:
                params[key] = filters[key]
    return params


def load_state(path, params, start, end):
    seconds = int((end - start).total_seconds())
    identity = {"version": CHECKPOINT_VERSION, "params": params, "seconds": seconds}
    if path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if all(loaded.get(key) == value for key, value in identity.items()):
            return loaded
    return {
        **identity, "start": iso(start), "end": iso(end),
        "completed": [], "vacancies": {}, "pending": [[iso(start), iso(end), 0]],
        "page": None, "requests": 0,
    }


def discover(account, params, start, end, state_path, max_requests=250, max_depth=24):
    """Keep the original date range and persist each accepted page before continuing."""
    path = Path(state_path)
    state = load_state(path, params, start, end)
    state.update(requests_last_run=0, overflow=[], issue=None)

    def save():
        atomic_write(path, json.dumps(state, ensure_ascii=False))

    save()
    while state["pending"] and state["requests_last_run"] < max_requests:
        a, b, depth = state["pending"][0]
        progress = state["page"]
        page = progress["next"] if progress else 0
        query = {**params, "date_from": a, "date_to": b}
        state["requests_last_run"] += 1
        state["requests"] += 1
        try:
            result = search_page(account, query, page)
        except hh_api.HHAPIError as exc:
            state["issue"] = {"code": "api_error", "status": exc.status, "detail": exc.detail}
            break

        if not isinstance(result, dict):
            result = {}
        found, pages, items = (result.get(key) for key in ("found", "pages", "items"))
        if type(found) is not int or found < 0 or type(pages) is not int or pages < 0 or not isinstance(items, list):
            state["page"] = None
            state["issue"] = {"code": "invalid_page", "page": page}
            break

        if found > SPLIT_AT or pages > MAX_PAGES:
            left, right = dt.datetime.fromisoformat(a), dt.datetime.fromisoformat(b)
            steps = (right - left) // DATE_RESOLUTION
            if depth < max_depth and steps >= 2:
                middle = iso(left + (steps // 2) * DATE_RESOLUTION)
                # Traverse the recent window first. Deduplicate the shared boundary by ID.
                state["pending"][:1] = [[middle, b, depth + 1], [a, middle, depth + 1]]
                state["page"] = None
                save()
                continue
            if found >= PAGE_SIZE * MAX_PAGES or pages > MAX_PAGES:
                state["overflow"] = [[a, b, found]]
                state["page"] = None
                break

        if progress is None:
            progress = {"next": 0, "found": found, "pages": pages, "vacancies": {}}
        expected_pages = (found + PAGE_SIZE - 1) // PAGE_SIZE
        expected_size = min(PAGE_SIZE, max(0, found - page * PAGE_SIZE))
        batch = {
            str(item["id"]): item for item in items
            if isinstance(item, dict) and str(item.get("id", "")).isdigit()
        }
        known = progress["vacancies"]
        if (
            (found, pages) != (progress["found"], progress["pages"])
            or (pages != expected_pages and not (found == 0 and pages == 1))
            or len(items) != expected_size
            or len(batch) != len(items)
            or not known.keys().isdisjoint(batch)
        ):
            state["page"] = None
            state["issue"] = {"code": "inconsistent_page", "page": page}
            break

        known.update(batch)
        if page + 1 < pages:
            progress["next"] += 1
            state["page"] = progress
        else:
            state["vacancies"].update(known)
            state["completed"].append([a, b, found])
            state["pending"].pop(0)
            state["page"] = None
        save()

    save()
    return state
