"""Runtime invitation identity and password acceptance under gym_runtime."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from django.contrib.auth.hashers import MD5PasswordHasher, PBKDF2PasswordHasher, make_password
from django.core import mail
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from apps.common.db.tenant_context import set_gym_context
from apps.tenants.exceptions import InvalidInvitation
from apps.tenants.models import Gym, GymMembership, Role, StaffInvitation
from apps.tenants.services import accept_owner_invitation, provision_gym_from_admin
from apps.tenants.staff_services import accept_staff_invitation, invite_staff
from apps.users.models import User
from tests.security.test_tenant_gym_settings import ensure_authorization_catalog

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

PASSWORD = "correct horse battery staple"
_TOKEN = re.compile(r"token=([A-Za-z0-9_-]+)")


def _approved_hash(password: str = PASSWORD) -> str:
    return make_password(password, hasher="pbkdf2_sha256")


@contextmanager
def _as_runtime() -> Iterator[None]:
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
        try:
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute("RESET ROLE")


def _token_for(recipient: str) -> str:
    message = next(item.body for item in mail.outbox if recipient in item.to)
    match = _TOKEN.search(message)
    assert match is not None
    return match.group(1)


def _deny(
    statement: str, params: list[object] | None = None, match: str = "permission denied"
) -> None:
    with (
        pytest.raises(DatabaseError, match=match),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute(statement, params)


def test_runtime_owner_and_staff_acceptance_sets_one_password() -> None:
    admin = User.objects.create_superuser("runtime-invite-admin@example.test", PASSWORD)
    existing = User.objects.create_user(
        "existing.runtime.staff@example.test",
        PASSWORD,
        first_name="Kept",
    )
    existing_hash = existing.password
    existing_changed_at = existing.password_changed_at
    plan = ensure_authorization_catalog()
    owner_email = f"runtime-owner-{uuid4().hex[:8]}@example.test"
    staff_email = f"runtime-staff-{uuid4().hex[:8]}@example.test"
    mail.outbox.clear()

    with _as_runtime():
        provision_gym_from_admin(
            actor=admin,
            idempotency_key=str(uuid4()),
            request_hash="a" * 64,
            gym_data={
                "code": f"RT{uuid4().hex[:6].upper()}",
                "name": "Runtime Invitation Gym",
                "timezone": "Asia/Yangon",
                "currency": plan.currency,
                "subscription_plan_id": plan.id,
                "subscription_status": "TRIAL",
            },
            owner_data={
                "email": owner_email,
                "first_name": "Runtime",
                "last_name": "Owner",
                "send_invitation": True,
            },
            request_id="req_runtime_provision",
        )
    owner = User.objects.get(normalized_email=owner_email)
    assert owner.email == owner.normalized_email == owner_email
    assert owner.is_active is True
    assert owner.is_platform_admin is False
    assert owner.is_superuser is False
    assert owner.mfa_enabled is False
    assert owner.mfa_secret_encrypted is None
    assert owner.mfa_last_used_step is None
    assert owner.last_login is None
    assert owner.last_login_at is None
    assert owner.has_usable_password() is False
    assert owner.password.startswith("!")
    assert owner.groups.count() == 0
    assert owner.user_permissions.count() == 0

    owner_token = _token_for(owner_email)
    with _as_runtime():
        membership = accept_owner_invitation(
            plaintext_token=owner_token,
            password=PASSWORD,
            request_id="req_runtime_owner_accept",
        )
    owner.refresh_from_db()
    membership.refresh_from_db()
    assert membership.status == GymMembership.Status.ACTIVE
    assert membership.is_owner is True
    assert owner.check_password(PASSWORD) is True
    assert owner.password_changed_at == owner.updated_at
    assert owner.is_platform_admin is False
    assert owner.is_superuser is False
    with pytest.raises(InvalidInvitation), _as_runtime():
        accept_owner_invitation(
            plaintext_token=owner_token,
            password=PASSWORD,
            request_id="req_runtime_owner_replay",
        )
    owner.refresh_from_db()
    assert owner.check_password(PASSWORD) is True
    invitation = StaffInvitation.objects.get(normalized_email=owner_email)
    assert invitation.status == StaffInvitation.Status.ACCEPTED
    with (
        pytest.raises(DatabaseError, match="invitation password transition denied"),
        _as_runtime(),
        transaction.atomic(),
    ):
        with connection.cursor() as cursor:
            set_gym_context(gym_id=invitation.gym_id)
            cursor.execute(
                "SELECT app_accept_invitation_password(%s, %s, %s)",
                [invitation.token_hash, _approved_hash(), timezone.now()],
            )

    gym = invitation.gym
    role_id = Role.objects.get(gym=gym, key="RECEPTIONIST").id
    owner_membership = GymMembership.objects.get(gym=gym, user=owner)
    mail.outbox.clear()
    with _as_runtime():
        invite_staff(
            gym_id=gym.id,
            actor=owner,
            actor_membership_id=owner_membership.id,
            staff_data={
                "email": staff_email,
                "first_name": "Runtime",
                "last_name": "Staff",
                "phone": "5550001111",
                "staff_code": f"RT-{uuid4().hex[:4]}",
                "display_name": "Runtime Staff",
                "joined_date": date(2026, 10, 1),
                "default_commission_percent": Decimal("0"),
            },
            role_ids=[role_id],
            request_id="req_runtime_staff_invite",
        )
    staff = User.objects.get(normalized_email=staff_email)
    assert staff.has_usable_password() is False
    assert staff.is_platform_admin is False
    assert staff.is_superuser is False
    assert staff.mfa_enabled is False
    staff_token = _token_for(staff_email)
    with _as_runtime():
        accept_staff_invitation(
            plaintext_token=staff_token,
            password=PASSWORD,
            confirmation={"first_name": "Accepted", "last_name": "Staff", "phone": "5550002222"},
            request_id="req_runtime_staff_accept",
        )
    staff.refresh_from_db()
    assert staff.first_name == "Accepted"
    assert staff.last_name == "Staff"
    assert staff.phone == "5550002222"
    assert staff.check_password(PASSWORD) is True
    assert staff.password_changed_at == staff.updated_at
    assert staff.is_platform_admin is False
    assert staff.is_superuser is False
    assert staff.mfa_enabled is False
    with pytest.raises(InvalidInvitation), _as_runtime():
        accept_staff_invitation(
            plaintext_token=staff_token,
            password="another correct horse battery",
            confirmation={"first_name": "Replayed"},
            request_id="req_runtime_staff_replay",
        )
    staff.refresh_from_db()
    assert staff.first_name == "Accepted"
    assert staff.check_password(PASSWORD) is True

    mail.outbox.clear()
    with _as_runtime():
        invite_staff(
            gym_id=gym.id,
            actor=owner,
            actor_membership_id=owner_membership.id,
            staff_data={
                "email": existing.email,
                "first_name": "Changed",
                "last_name": "Person",
                "staff_code": f"EX-{uuid4().hex[:4]}",
                "display_name": "Existing Staff",
                "joined_date": date(2026, 10, 2),
                "default_commission_percent": Decimal("0"),
            },
            role_ids=[role_id],
            request_id="req_runtime_existing_invite",
        )
    existing.refresh_from_db()
    assert existing.first_name == "Kept"
    assert existing.password == existing_hash
    existing_token = _token_for(existing.email)
    with pytest.raises(InvalidInvitation), _as_runtime():
        accept_staff_invitation(
            plaintext_token=existing_token,
            password="another correct horse battery",
            confirmation={"first_name": "Nope"},
            request_id="req_runtime_existing_wrong",
        )
    existing.refresh_from_db()
    assert existing.password == existing_hash
    assert existing.password_changed_at == existing_changed_at
    assert existing.first_name == "Kept"
    assert (
        StaffInvitation.objects.get(normalized_email=existing.email).status
        == StaffInvitation.Status.PENDING
    )
    with _as_runtime():
        accept_staff_invitation(
            plaintext_token=existing_token,
            password=PASSWORD,
            confirmation={},
            request_id="req_runtime_existing_accept",
        )
    existing.refresh_from_db()
    assert existing.password == existing_hash
    assert existing.password_changed_at == existing_changed_at
    assert existing.first_name == "Kept"
    assert existing.check_password(PASSWORD) is True


def test_runtime_password_function_accepts_only_current_pbkdf2() -> None:
    gym = Gym.objects.create(code=f"FH{uuid4().hex[:6].upper()}", name="Hash Gym")

    def pending_user(email: str) -> tuple[User, StaffInvitation]:
        user = User.objects.create_user(email, None)
        user.set_unusable_password()
        user.save(update_fields=["password", "updated_at"])
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            membership = GymMembership.objects.create(
                gym=gym,
                user=user,
                status=GymMembership.Status.INVITED,
                is_owner=False,
                joined_date=date(2026, 10, 1),
                invited_at=timezone.now(),
            )
            invitation = StaffInvitation.objects.create(
                gym=gym,
                gym_membership=membership,
                email=user.email,
                normalized_email=user.normalized_email,
                token_hash=uuid4().hex + uuid4().hex,
                expires_at=timezone.now() + timedelta(hours=1),
                status=StaffInvitation.Status.PENDING,
            )
        return user, invitation

    user, invitation = pending_user("runtime-hash-target@example.test")
    stronger_user, stronger_invitation = pending_user("runtime-stronger-hash@example.test")

    def submit(target: StaffInvitation, encoded: str) -> None:
        with connection.cursor() as cursor:
            set_gym_context(gym_id=gym.id)
            cursor.execute(
                "SELECT app_accept_invitation_password(%s, %s, %s)",
                [target.token_hash, encoded, timezone.now()],
            )

    approved = _approved_hash()
    hasher = PBKDF2PasswordHasher()
    salt = hasher.salt()
    digest = approved.split("$", 3)[3]
    md5 = MD5PasswordHasher()
    denied = [
        md5.encode(PASSWORD, md5.salt()),
        hasher.encode(PASSWORD, salt, iterations=100_000),
        hasher.encode(PASSWORD, hasher.salt(), iterations=999_999),
        f"pbkdf2_sha256$1000000${salt[:-1]}${digest}",
        f"pbkdf2_sha256$1000000${salt}${digest[:-1]}",
        f"{approved}$extra",
        approved.replace("pbkdf2_sha256", "pbkdf2_sha1", 1),
        f"pbkdf2_sha256$01000000${salt}${digest}",
    ]

    def expect_denied(encoded: str) -> None:
        with (
            pytest.raises(DatabaseError, match="invitation password transition denied"),
            _as_runtime(),
            transaction.atomic(),
        ):
            submit(invitation, encoded)

    for encoded in denied:
        expect_denied(encoded)

    user.refresh_from_db()
    assert user.has_usable_password() is False
    with _as_runtime():
        submit(invitation, approved)
    user.refresh_from_db()
    assert user.password == approved
    assert user.password.startswith("pbkdf2_sha256$1000000$")
    assert user.check_password(PASSWORD) is True

    above_minimum = hasher.encode(PASSWORD, hasher.salt(), iterations=1_000_001)
    with _as_runtime():
        submit(stronger_invitation, above_minimum)
    stronger_user.refresh_from_db()
    assert stronger_user.password == above_minimum
    assert stronger_user.check_password(PASSWORD) is True


def test_runtime_password_function_rejects_uninvited_transitions() -> None:
    gym = Gym.objects.create(code=f"FN{uuid4().hex[:6].upper()}", name="Function Gym")
    other = Gym.objects.create(code=f"FO{uuid4().hex[:6].upper()}", name="Foreign Gym")
    platform = User.objects.create_superuser("runtime-platform-target@example.test", PASSWORD)
    usable = User.objects.create_user("runtime-usable-target@example.test", PASSWORD)
    passwordless = User.objects.create_user("runtime-passwordless-target@example.test", None)
    passwordless.set_unusable_password()
    passwordless.save(update_fields=["password", "updated_at"])
    usable_hash = usable.password
    platform_hash = platform.password

    def invite(user: User, *, status: str = "PENDING", **overrides: object) -> StaffInvitation:
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            membership = GymMembership.objects.create(
                gym=gym,
                user=user,
                status=GymMembership.Status.INVITED,
                is_owner=False,
                joined_date=date(2026, 10, 1),
                invited_at=timezone.now(),
            )
            values: dict[str, object] = {
                "gym": gym,
                "gym_membership": membership,
                "email": user.email,
                "normalized_email": user.normalized_email,
                "token_hash": uuid4().hex + uuid4().hex,
                "expires_at": timezone.now() + timedelta(hours=1),
                "status": status,
            }
            values.update(overrides)
            return StaffInvitation.objects.create(**values)

    pending = invite(passwordless)
    expired = invite(
        User.objects.create_user("runtime-expired-target@example.test", None),
        expires_at=timezone.now() - timedelta(seconds=5),
    )
    User.objects.filter(normalized_email="runtime-expired-target@example.test").update(
        password="!" + "e" * 40
    )
    accepted = invite(
        User.objects.create_user("runtime-accepted-target@example.test", None),
        status=StaffInvitation.Status.ACCEPTED,
        accepted_at=timezone.now(),
    )
    User.objects.filter(normalized_email="runtime-accepted-target@example.test").update(
        password="!" + "a" * 40
    )
    revoked = invite(
        User.objects.create_user("runtime-revoked-target@example.test", None),
        status=StaffInvitation.Status.REVOKED,
        revoked_at=timezone.now(),
    )
    User.objects.filter(normalized_email="runtime-revoked-target@example.test").update(
        password="!" + "r" * 40
    )
    platform_invite = invite(platform)
    usable_invite = invite(usable)
    encoded = _approved_hash()

    def transition(invitation: StaffInvitation, gym_id: object = gym.id) -> None:
        with connection.cursor() as cursor:
            set_gym_context(gym_id=gym_id)  # type: ignore[arg-type]
            cursor.execute(
                "SELECT app_accept_invitation_password(%s, %s, %s)",
                [invitation.token_hash, encoded, timezone.now()],
            )

    def expect_denied(match: str, operation: object) -> None:
        with pytest.raises(DatabaseError, match=match), _as_runtime(), transaction.atomic():
            operation()  # type: ignore[operator]

    expect_denied("invitation password transition denied", lambda: transition(pending, other.id))
    expect_denied("invitation password transition denied", lambda: transition(expired))
    expect_denied("invitation password transition denied", lambda: transition(accepted))
    expect_denied("invitation password transition denied", lambda: transition(revoked))
    expect_denied("invitation password transition denied", lambda: transition(platform_invite))
    expect_denied("invitation password transition denied", lambda: transition(usable_invite))

    def missing_token() -> None:
        with connection.cursor() as cursor:
            set_gym_context(gym_id=gym.id)
            cursor.execute(
                "SELECT app_accept_invitation_password(%s, %s, %s)",
                ["ab" * 32, encoded, timezone.now()],
            )

    def plaintext_password() -> None:
        with connection.cursor() as cursor:
            set_gym_context(gym_id=gym.id)
            cursor.execute(
                "SELECT app_accept_invitation_password(%s, %s, %s)",
                [pending.token_hash, "plaintext-password", timezone.now()],
            )

    def foreign_profile() -> None:
        with connection.cursor() as cursor:
            set_gym_context(gym_id=other.id)
            cursor.execute(
                "SELECT app_confirm_invited_profile(%s, %s, %s, %s, %s)",
                [pending.token_hash, "Escalated", "Name", "", timezone.now()],
            )

    def malformed_identity() -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT app_create_invited_identity(%s, %s, %s, %s)",
                ["Not.Lower@example.test", "Bad", "Email", ""],
            )

    expect_denied("invitation password transition denied", missing_token)
    expect_denied("invitation password transition denied", plaintext_password)
    expect_denied("invited profile confirmation denied", foreign_profile)
    expect_denied("invited identity requires a normalized email", malformed_identity)

    passwordless.refresh_from_db()
    platform.refresh_from_db()
    usable.refresh_from_db()
    assert passwordless.has_usable_password() is False
    assert platform.password == platform_hash
    assert platform.is_platform_admin is True
    assert usable.password == usable_hash
    user_id = passwordless.id
    with _as_runtime():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT app_create_invited_identity(%s, %s, %s, %s)",
                ["direct-runtime-owner@example.test", "Direct", "Owner", ""],
            )
            created_id = cursor.fetchone()[0]
    direct = User.objects.get(pk=created_id)
    assert direct.has_usable_password() is False
    assert direct.is_active is True
    assert direct.is_platform_admin is False
    assert direct.is_superuser is False
    assert direct.mfa_enabled is False
    assert direct.mfa_secret_encrypted is None
    assert direct.last_login is None
    assert direct.last_login_at is None
    assert direct.email == direct.normalized_email

    _deny(
        "INSERT INTO users_user (id, password, created_at, updated_at, email, normalized_email, "
        "first_name, last_name, phone, is_active, is_platform_admin, is_superuser, mfa_enabled) "
        "VALUES (gen_random_uuid(), %s, now(), now(), %s, %s, '', '', '', true, true, true, true)",
        [encoded, "raw-admin@example.test", "raw-admin@example.test"],
    )
    _deny(
        "UPDATE users_user SET password = %s, password_changed_at = now(), updated_at = now() "
        "WHERE id = %s",
        [encoded, user_id],
    )
    _deny("UPDATE users_user SET is_platform_admin = true WHERE id = %s", [user_id])
    _deny("UPDATE users_user SET is_superuser = true WHERE id = %s", [user_id])
    _deny(
        "UPDATE users_user SET first_name = 'Renamed', updated_at = now() WHERE id = %s",
        [user_id],
    )


def test_runtime_insert_trigger_rejects_omitted_privilege_columns() -> None:
    encoded = make_password(PASSWORD)
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("GRANT INSERT ON users_user TO gym_runtime")
            cursor.execute("SET LOCAL ROLE gym_runtime")
            with (
                pytest.raises(DatabaseError, match="ordinary invited identity"),
                transaction.atomic(),
            ):
                cursor.execute(
                    """
                    INSERT INTO users_user (
                      id, password, created_at, updated_at, email, normalized_email,
                      first_name, last_name, phone
                    ) VALUES (
                      gen_random_uuid(), %s, clock_timestamp(), clock_timestamp(),
                      'omitted-flags@example.test', 'omitted-flags@example.test',
                      'Omitted', 'Flags', ''
                    )
                    """,
                    [encoded],
                )
            with (
                pytest.raises(DatabaseError, match="ordinary invited identity"),
                transaction.atomic(),
            ):
                cursor.execute(
                    """
                    INSERT INTO users_user (
                      id, password, created_at, updated_at, email, normalized_email,
                      first_name, last_name, phone
                    ) VALUES (
                      gen_random_uuid(), '!omitted-flags-are-not-safe', clock_timestamp(),
                      clock_timestamp(), 'omitted-unusable@example.test',
                      'omitted-unusable@example.test', 'Omitted', 'Unusable', ''
                    )
                    """
                )
            with (
                pytest.raises(DatabaseError, match="ordinary invited identity"),
                transaction.atomic(),
            ):
                cursor.execute(
                    """
                    INSERT INTO users_user (
                      id, password, last_login, is_superuser, created_at, updated_at,
                      email, normalized_email, first_name, last_name, phone, is_active,
                      is_platform_admin, mfa_enabled, mfa_secret_encrypted,
                      mfa_last_used_step, last_login_at, password_changed_at
                    ) VALUES (
                      gen_random_uuid(), '!ordinary-but-admin', NULL, false,
                      clock_timestamp(), clock_timestamp(), 'explicit-admin@example.test',
                      'explicit-admin@example.test', 'Explicit', 'Admin', '', true,
                      true, false, NULL, NULL, NULL, clock_timestamp()
                    )
                    """
                )
            with (
                pytest.raises(DatabaseError, match="ordinary invited identity"),
                transaction.atomic(),
            ):
                cursor.execute(
                    """
                    INSERT INTO users_user (
                      id, password, last_login, is_superuser, created_at, updated_at,
                      email, normalized_email, first_name, last_name, phone, is_active,
                      is_platform_admin, mfa_enabled, mfa_secret_encrypted,
                      mfa_last_used_step, last_login_at, password_changed_at
                    ) VALUES (
                      gen_random_uuid(), '!ordinary-session', clock_timestamp(), true,
                      clock_timestamp(), clock_timestamp(), 'session-state@example.test',
                      'session-state@example.test', 'Session', 'State', '', true,
                      false, true, 'secret', 1, clock_timestamp(), clock_timestamp()
                    )
                    """
                )
            with (
                pytest.raises(DatabaseError, match="ordinary invited identity"),
                transaction.atomic(),
            ):
                cursor.execute(
                    """
                    INSERT INTO users_user (
                      id, password, last_login, is_superuser, created_at, updated_at,
                      email, normalized_email, first_name, last_name, phone, is_active,
                      is_platform_admin, mfa_enabled, mfa_secret_encrypted,
                      mfa_last_used_step, last_login_at, password_changed_at
                    ) VALUES (
                      gen_random_uuid(), '!ordinary-mail', NULL, false,
                      clock_timestamp(), clock_timestamp(), 'Mixed.Case@example.test',
                      'mixed.case@example.test', 'Mixed', 'Case', '', true,
                      false, false, NULL, NULL, NULL, clock_timestamp()
                    )
                    """
                )
            with (
                pytest.raises(DatabaseError, match="ordinary invited identity"),
                transaction.atomic(),
            ):
                cursor.execute(
                    """
                    INSERT INTO users_user (
                      id, password, last_login, is_superuser, created_at, updated_at,
                      email, normalized_email, first_name, last_name, phone, is_active,
                      is_platform_admin, mfa_enabled, mfa_secret_encrypted,
                      mfa_last_used_step, last_login_at, password_changed_at
                    ) VALUES (
                      gen_random_uuid(), %s, NULL, false, clock_timestamp(),
                      clock_timestamp(), 'usable-password@example.test',
                      'usable-password@example.test', 'Usable', 'Password', '', true,
                      false, false, NULL, NULL, NULL, clock_timestamp()
                    )
                    """,
                    [encoded],
                )
            cursor.execute(
                """
                INSERT INTO users_user (
                  id, password, last_login, is_superuser, created_at, updated_at,
                  email, normalized_email, first_name, last_name, phone, is_active,
                  is_platform_admin, mfa_enabled, mfa_secret_encrypted, mfa_last_used_step,
                  last_login_at, password_changed_at
                ) VALUES (
                  gen_random_uuid(), '!ordinary-invited-identity', NULL, false,
                  clock_timestamp(), clock_timestamp(), 'ordinary-runtime@example.test',
                  'ordinary-runtime@example.test', 'Ordinary', 'Runtime', '', true,
                  false, false, NULL, NULL, NULL, clock_timestamp()
                )
                RETURNING is_active, is_platform_admin, is_superuser, mfa_enabled,
                          mfa_secret_encrypted, mfa_last_used_step, last_login, last_login_at,
                          left(password, 1), email, normalized_email
                """
            )
            row = cursor.fetchone()
            assert row == (
                True,
                False,
                False,
                False,
                None,
                None,
                None,
                None,
                "!",
                "ordinary-runtime@example.test",
                "ordinary-runtime@example.test",
            )
            cursor.execute("RESET ROLE")
            cursor.execute("REVOKE INSERT ON users_user FROM gym_runtime")
            cursor.execute(
                "GRANT UPDATE (password, password_changed_at, first_name) "
                "ON users_user TO gym_runtime"
            )
            cursor.execute("SET LOCAL ROLE gym_runtime")
            with (
                pytest.raises(DatabaseError, match="cannot change identity, password"),
                transaction.atomic(),
            ):
                cursor.execute(
                    "UPDATE users_user SET password = %s, password_changed_at = clock_timestamp() "
                    "WHERE email = 'ordinary-runtime@example.test'",
                    [encoded],
                )
            with (
                pytest.raises(DatabaseError, match="cannot change identity, password"),
                transaction.atomic(),
            ):
                cursor.execute(
                    "UPDATE users_user SET first_name = 'Renamed' "
                    "WHERE email = 'ordinary-runtime@example.test'"
                )
            cursor.execute(
                "UPDATE users_user SET mfa_last_used_step = 4, updated_at = clock_timestamp() "
                "WHERE email = 'ordinary-runtime@example.test'"
            )
            cursor.execute(
                "SELECT left(password, 1), is_platform_admin, mfa_last_used_step "
                "FROM users_user WHERE email = 'ordinary-runtime@example.test'"
            )
            assert cursor.fetchone() == ("!", False, 4)
            cursor.execute("RESET ROLE")
            cursor.execute(
                "REVOKE UPDATE (password, password_changed_at, first_name) "
                "ON users_user FROM gym_runtime"
            )
