from __future__ import annotations

from django.core.cache import cache


def test_cache_value_is_visible_inside_its_own_test() -> None:
    cache.set("cross-test-probe", "present", 60)
    assert cache.get("cross-test-probe") == "present"


def test_cache_value_does_not_survive_into_the_next_test() -> None:
    assert cache.get("cross-test-probe") is None
