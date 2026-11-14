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


@pytest.fixture(autouse=True)
def entitle_assistant_test_gyms(request: pytest.FixtureRequest) -> Iterator[None]:
    """Keep existing assistant tests on an entitled gym.

    Production gyms stay AI-disabled until a platform sale selects AI.
    Assistant tests create gyms to exercise tools, so those creates are
    entitled here. The subscription tests live outside this filename prefix.
    """

    name = request.node.path.name
    if not name.startswith(("test_assistant", "test_unit_assistant")):
        yield
        return
    from django.db.models.signals import pre_save

    from apps.tenants.models import Gym

    def entitle(sender: type[Gym], instance: Gym, **kwargs: object) -> None:
        del sender, kwargs
        if instance._state.adding:
            instance.ai_enabled = True

    pre_save.connect(entitle, sender=Gym)
    yield
    pre_save.disconnect(entitle, sender=Gym)
