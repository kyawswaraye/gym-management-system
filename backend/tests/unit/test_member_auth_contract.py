from __future__ import annotations

from uuid import uuid4

from apps.users.serializers import MemberLoginInputSerializer


def test_member_login_rejects_a_caller_supplied_member_or_gym() -> None:
    serializer = MemberLoginInputSerializer(
        data={
            "email": "member@example.test",
            "password": "correct horse battery staple",
            "member_id": str(uuid4()),
            "gym_id": str(uuid4()),
        }
    )
    assert not serializer.is_valid()
    assert set(serializer.errors) == {"member_id", "gym_id"}
