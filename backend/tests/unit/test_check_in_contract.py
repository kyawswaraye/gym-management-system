from datetime import UTC, datetime, timedelta

import pytest
from rest_framework.exceptions import ValidationError

from apps.members.challenge_services import CHALLENGE_PAYLOAD_PREFIX, clean_display_location
from apps.members.checkin_services import (
    CHECK_IN_BACKDATE_LIMIT,
    CHECK_IN_CLOCK_SKEW,
    validate_manual_occurred_at,
)
from apps.members.views import (
    CheckInChallengeIssueView,
    CheckInCheckoutView,
    CheckInListCreateView,
    MemberCheckInHistoryView,
)


def test_manual_backdate_is_timezone_aware_and_bounded() -> None:
    now = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
    accepted = validate_manual_occurred_at(now - timedelta(hours=23), now=now)
    assert accepted == now - timedelta(hours=23)
    edge = validate_manual_occurred_at(now + CHECK_IN_CLOCK_SKEW, now=now)
    assert edge == now + CHECK_IN_CLOCK_SKEW
    with pytest.raises(ValidationError) as future:
        validate_manual_occurred_at(now + CHECK_IN_CLOCK_SKEW + timedelta(seconds=1), now=now)
    assert "future" in str(future.value.detail["occurred_at"][0])
    with pytest.raises(ValidationError) as old:
        validate_manual_occurred_at(now - CHECK_IN_BACKDATE_LIMIT - timedelta(seconds=1), now=now)
    assert "24 hours" in str(old.value.detail["occurred_at"][0])
    with pytest.raises(ValidationError):
        validate_manual_occurred_at(datetime(2026, 10, 17, 12, 0), now=now)
    checkout = validate_manual_occurred_at(
        now - timedelta(hours=23),
        now=now,
        action="Check-out",
    )
    assert checkout == now - timedelta(hours=23)
    with pytest.raises(ValidationError) as checkout_old:
        validate_manual_occurred_at(
            now - CHECK_IN_BACKDATE_LIMIT - timedelta(seconds=1),
            now=now,
            action="Check-out",
        )
    assert "Check-out can only be backdated 24 hours." in str(
        checkout_old.value.detail["occurred_at"][0]
    )


def test_manual_check_in_views_use_member_and_membership_entitlements() -> None:
    assert CheckInListCreateView.required_features == ("members", "memberships")
    assert CheckInListCreateView.required_permission == "checkins.view"
    assert CheckInCheckoutView.required_permission == "checkins.update"
    assert MemberCheckInHistoryView.required_permissions == ("members.view", "checkins.view")
    assert MemberCheckInHistoryView.required_features == ("members", "memberships")
    assert CheckInChallengeIssueView.required_permission == "checkins.challenge.issue"
    assert CheckInChallengeIssueView.required_features == ("members", "memberships")
    assert clean_display_location("  Front   desk ") == "Front desk"
    assert CHALLENGE_PAYLOAD_PREFIX == "gms-checkin:v1:"
    with pytest.raises(ValidationError):
        clean_display_location("  ")
