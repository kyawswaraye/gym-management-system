"""Process-wide test isolation for Django's local cache.

Transactional tests roll back database rows and leave the cache in place.
DRF throttle buckets live in that cache and are keyed partly by client
address, which every API client in this process shares. Clearing around each
test keeps one test from exhausting another test's limit. Values written
during a test remain until that test ends.
"""

from collections.abc import Iterator

import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def isolate_django_cache() -> Iterator[None]:
    cache.clear()
    yield
    cache.clear()
