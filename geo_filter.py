"""Conservative geo/remote eligibility gate before any HH application.

Unknown or ambiguous geography/format is rejected rather than risking an
out-of-city office application. HH area IDs are stable identifiers.
"""


def _id(value):
    if isinstance(value, dict):
        value = value.get("id")
    return str(value or "").strip()


def _format_ids(vacancy):
    formats = vacancy.get("work_format")
    if not isinstance(formats, list):
        return set()
    return {_id(item).upper() for item in formats if _id(item)}


def eligible(vacancy, home_area):
    """Return (allowed, reason); never infer remote from schedule alone."""
    area = vacancy.get("area")
    area_id = _id(area)
    home_id = str(home_area)
    if area_id == home_id:
        return True, "home_city"
    formats = _format_ids(vacancy)
    # A vacancy can list multiple formats. Only explicitly remote-only
    # listings are safe outside the home city.
    if formats == {"REMOTE"}:
        return True, "remote_outside_city"
    if not area_id:
        return False, "unknown_area_or_format"
    if not formats:
        return False, "unknown_work_format"
    return False, "nonremote_outside_city"
