"""Bounded, resumable HH search discovery using publication-time partitions.

This module only discovers candidates. Applying still goes through upstream's
normal duplicate, eligibility and response checks.
"""
import datetime as dt
import json
import os
from pathlib import Path
from urllib.parse import urlencode

import hh_api
from storage import atomic_write

UTC = dt.timezone.utc


def iso(value):
    return value.isoformat(timespec="seconds")


def search_page(account, params, page):
    return hh_api.request(account, "vacancies?" + urlencode({**params, "page": page, "per_page": 100}))


def discover(account, params, start, end, state_path, max_requests=250, max_depth=12):
    """Resume completed windows; never claim complete when capped or interrupted."""
    state_path = Path(state_path)
    state = {"completed": [], "vacancies": {}, "pending": [], "requests": 0}
    if state_path.exists():
        loaded = json.loads(state_path.read_text(encoding="utf-8"))
        if loaded.get("params") == params and loaded.get("start") == iso(start) and loaded.get("end") == iso(end):
            state.update(loaded)
    else:
        state["pending"] = [[iso(start), iso(end), 0]]
    state.update(params=params, start=iso(start), end=iso(end))
    requests = 0

    def save():
        atomic_write(state_path, json.dumps(state, ensure_ascii=False))

    while state["pending"] and requests < max_requests:
        a, b, depth = state["pending"][0]
        query = {**params, "date_from": a, "date_to": b}
        first = search_page(account, query, 0)
        requests += 1
        found = int(first.get("found", 0))
        pages = int(first.get("pages", 0))
        if (found > 1900 or pages > 20) and depth < max_depth:
            left, right = dt.datetime.fromisoformat(a), dt.datetime.fromisoformat(b)
            if (right-left).total_seconds() > 2:
                middle = iso(left+(right-left)/2)
                state["pending"] = [[a, middle, depth+1], [middle, b, depth+1]] + state["pending"][1:]
                save()
                continue
        if found > 1900 or pages > 20:
            state.setdefault("overflow", []).append([a, b, found])
            state["pending"].pop(0)
            save()
            continue
        # Persist only after all pages in a window were retrieved successfully.
        batch = list(first.get("items") or [])
        complete = True
        for page in range(1, pages):
            if requests >= max_requests:
                complete = False
                break
            result = search_page(account, query, page)
            requests += 1
            batch.extend(result.get("items") or [])
        if not complete:
            save()
            break
        for vacancy in batch:
            vid = str(vacancy.get("id", ""))
            if vid.isdigit():
                state["vacancies"][vid] = vacancy
        state["completed"].append([a, b, found])
        state["pending"].pop(0)
        state["requests"] += requests
        save()
    return state
