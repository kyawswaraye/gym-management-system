from django.test import override_settings

from apps.users.services import hash_refresh_token, hash_selection_ticket


def test_refresh_hash_is_peppered_and_deterministic() -> None:
    with override_settings(REFRESH_TOKEN_PEPPER="pepper-one"):
        first = hash_refresh_token("opaque-secret")
        assert first == hash_refresh_token("opaque-secret")
    with override_settings(REFRESH_TOKEN_PEPPER="pepper-two"):
        assert hash_refresh_token("opaque-secret") != first
    assert "opaque-secret" not in first
    assert len(first) == 64


def test_selection_ticket_hash_matches_refresh_construction() -> None:
    with override_settings(REFRESH_TOKEN_PEPPER="pepper-one"):
        assert hash_selection_ticket("opaque-secret") == hash_refresh_token("opaque-secret")
