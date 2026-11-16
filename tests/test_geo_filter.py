"""Geographic eligibility regression tests."""
import geo_filter
import profiles_lib
import pytest


@pytest.mark.parametrize("vacancy,allowed,reason", [
    ({"area": {"id": "1"}, "work_format": [{"id": "ON_SITE"}]}, True, "home_city"),
    ({"area": {"id": "1"}, "work_format": [{"id": "HYBRID"}]}, True, "home_city"),
    ({"area": {"id": "2"}, "work_format": [{"id": "REMOTE"}]}, True, "remote_outside_city"),
    ({"area": {"id": "2"}, "work_format": [{"id": "ON_SITE"}]}, False, "nonremote_outside_city"),
    ({"area": {"id": "2"}, "work_format": [{"id": "HYBRID"}]}, False, "nonremote_outside_city"),
    ({"area": {"id": "2"}, "work_format": [{"id": "REMOTE"}, {"id": "HYBRID"}]}, False, "nonremote_outside_city"),
    ({"area": {"id": "2"}, "schedule": {"id": "remote"}}, False, "unknown_work_format"),
    ({"area": {"id": "2"}}, False, "unknown_work_format"),
    ({"work_format": [{"id": "ON_SITE"}]}, False, "unknown_area_or_format"),
    ({"work_format": [{"id": "REMOTE"}]}, True, "remote_outside_city"),
])
def test_geo_eligibility(vacancy, allowed, reason):
    assert geo_filter.eligible(vacancy, 1) == (allowed, reason)


def test_geo_defaults_disabled():
    filters = profiles_lib.get_filters({})
    assert filters["geo_enabled"] is False
    assert filters["geo_home_area"] == 0
