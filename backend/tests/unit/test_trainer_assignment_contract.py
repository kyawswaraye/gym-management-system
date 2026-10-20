from datetime import date
from uuid import uuid4

from apps.trainers.models import TrainerAssignment
from apps.trainers.serializers import (
    AssignmentCancelSerializer,
    AssignmentCreateSerializer,
    AssignmentEndSerializer,
    AssignmentListQuerySerializer,
)
from apps.trainers.views import (
    TrainerAssignedMembersView,
    TrainerAssignmentCancelView,
    TrainerAssignmentDetailView,
    TrainerAssignmentEndView,
    TrainerAssignmentListCreateView,
    TrainerListView,
)

MEMBER_ID = "7df4f2d6-79d3-4fd1-b850-968142b1b6dd"
TRAINER_ID = "8df4f2d6-79d3-4fd1-b850-968142b1b6dd"


def _create(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "trainer_staff_profile_id": TRAINER_ID,
        "member_id": MEMBER_ID,
        "effective_from": "2026-10-19",
    }
    body.update(overrides)
    return body


def test_assignment_routes_require_pt_permission_and_entitlement() -> None:
    assert TrainerListView.required_permission == "pt.assignments.view"
    assert TrainerListView.required_feature == "pt"
    assert TrainerAssignedMembersView.required_permission == "pt.assignments.view"
    assert TrainerAssignmentDetailView.required_permission == "pt.assignments.view"
    assert TrainerAssignmentEndView.required_permission == "pt.assignments.manage"
    assert TrainerAssignmentCancelView.required_permission == "pt.assignments.manage"
    assert TrainerAssignmentEndView.required_feature == "pt"
    assert TrainerAssignmentListCreateView.required_permission == "pt.assignments.view"
    assert TrainerAssignment.Status.values == ["ACTIVE", "ENDED", "CANCELLED"]


def test_assignment_create_rejects_server_fields() -> None:
    rejected = AssignmentCreateSerializer(
        data=_create(
            gym_id=str(uuid4()),
            status="ENDED",
            ended_at="2026-10-19T00:00:00Z",
            created_by_membership_id=str(uuid4()),
            id=str(uuid4()),
        )
    )

    assert not rejected.is_valid()
    assert set(rejected.errors) >= {
        "gym_id",
        "status",
        "ended_at",
        "created_by_membership_id",
        "id",
    }
    accepted = AssignmentCreateSerializer(
        data=_create(effective_until="2026-10-20", reason="  Floor program  ")
    )
    assert accepted.is_valid(), accepted.errors
    assert accepted.validated_data["effective_from"] == date(2026, 10, 19)
    assert accepted.validated_data["effective_until"] == date(2026, 10, 20)
    assert accepted.validated_data["reason"] == "Floor program"
    omitted = AssignmentCreateSerializer(data=_create())
    assert omitted.is_valid(), omitted.errors
    assert omitted.validated_data["effective_until"] is None
    assert omitted.validated_data["reason"] == ""


def test_end_and_cancel_fields_follow_the_command_contract() -> None:
    ended = AssignmentEndSerializer(data={"effective_until": "2026-10-19", "reason": " Completed "})
    assert ended.is_valid(), ended.errors
    assert ended.validated_data["reason"] == "Completed"
    blank = AssignmentEndSerializer(data={"effective_until": "2026-10-19", "reason": "   "})
    assert not blank.is_valid()
    assert "reason" in blank.errors
    missing_until = AssignmentEndSerializer(data={"reason": "Done"})
    assert not missing_until.is_valid()
    assert "effective_until" in missing_until.errors
    cancelled = AssignmentCancelSerializer(data={"reason": " Mistake "})
    assert cancelled.is_valid(), cancelled.errors
    extra = AssignmentCancelSerializer(
        data={"reason": "Mistake", "effective_until": "2026-10-19", "status": "CANCELLED"}
    )
    assert not extra.is_valid()
    assert set(extra.errors) >= {"effective_until", "status"}


def test_assignment_list_query_is_allowlisted() -> None:
    accepted = AssignmentListQuerySerializer(
        data={"status": "ACTIVE", "trainer_staff_profile_id": TRAINER_ID, "limit": "10"}
    )
    assert accepted.is_valid(), accepted.errors
    assert accepted.validated_data["status"] == "ACTIVE"
    rejected = AssignmentListQuerySerializer(data={"gym_id": TRAINER_ID, "ordering": "name"})
    assert not rejected.is_valid()
    assert set(rejected.errors) >= {"gym_id", "ordering"}
