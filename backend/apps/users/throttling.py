from __future__ import annotations

from typing import Any

from rest_framework.request import Request
from rest_framework.settings import api_settings
from rest_framework.throttling import SimpleRateThrottle

from apps.audit.models import Outcome
from apps.audit.services import audit_ticket, write_platform_audit

from .models import GymSelectionTicket, User
from .services import hash_refresh_token, hash_selection_ticket


class _ConfiguredRateThrottle(SimpleRateThrottle):
    def get_rate(self) -> str | None:
        if self.scope is None:
            return None
        rate = api_settings.DEFAULT_THROTTLE_RATES.get(self.scope)
        return str(rate) if rate is not None else None

    def _cache_key(self, *, fingerprint: str, request: Request) -> str:
        source = self.get_ident(request)
        return self.cache_format % {
            "scope": self.scope,
            "ident": f"{fingerprint}:{source}",
        }


class DeviceChallengeSelectorThrottle(_ConfiguredRateThrottle):
    """Limit device challenge minting by selector fingerprint and address.

    The cache key is a hash of the public selector. The secret is not read
    into the key.
    """

    scope = "device_challenge_selector"

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        header = request.headers.get("Authorization", "")
        marker = "gms-device:v1:"
        start = header.find(marker)
        if start < 0:
            return None
        remainder = header[start + len(marker) :]
        selector = remainder.split(".", 1)[0]
        if not selector or len(selector) > 64:
            return None
        fingerprint = hash_refresh_token(f"device-challenge:{selector}")
        return self._cache_key(fingerprint=fingerprint, request=request)


class _AddressBoundaryThrottle(_ConfiguredRateThrottle):
    """Limit one route family by client address.

    ``boundary`` is hashed into the cache key and ``scope`` selects the rate.
    Neither value is the shared anonymous scope, so this bucket cannot be
    exhausted by another route family from the same address.
    """

    boundary: str

    def get_cache_key(self, request: Request, view: Any) -> str:
        fingerprint = hash_refresh_token(self.boundary)
        return self._cache_key(fingerprint=fingerprint, request=request)


class _PresentedTokenThrottle(_ConfiguredRateThrottle):
    """Limit one route by a peppered hash of the presented token and address.

    The raw token is not the cache key.
    """

    boundary: str

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        token = request.data.get("refresh_token") if isinstance(request.data, dict) else None
        if not isinstance(token, str) or not token:
            return None
        fingerprint = hash_refresh_token(f"{self.boundary}:{token}")
        return self._cache_key(fingerprint=fingerprint, request=request)


class DeviceChallengeIPThrottle(_AddressBoundaryThrottle):
    """Bound device challenge attempts from one address, including malformed ones."""

    scope = "device_challenge_ip"
    boundary = "device-challenge-ip"


class DeviceIngestSelectorThrottle(_ConfiguredRateThrottle):
    """Limit ingestion by selector fingerprint and address. The secret is not a key."""

    scope = "device_ingest_selector"

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        header = request.headers.get("Authorization", "")
        marker = "gms-device:v1:"
        start = header.find(marker)
        if start < 0:
            return None
        selector = header[start + len(marker) :].split(".", 1)[0]
        if not selector or len(selector) > 64:
            return None
        fingerprint = hash_refresh_token(f"device-ingest:{selector}")
        return self._cache_key(fingerprint=fingerprint, request=request)


class DeviceIngestIPThrottle(_AddressBoundaryThrottle):
    """Bound ingestion attempts from one address, including malformed ones."""

    scope = "device_ingest_ip"
    boundary = "device-ingest-ip"


class MemberCheckInScanThrottle(_ConfiguredRateThrottle):
    """Limit member QR scans by the authenticated user, never by the payload."""

    scope = "member_check_in_scan"

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        user = getattr(request, "user", None)
        user_id = getattr(user, "pk", None)
        if not user_id:
            return None
        fingerprint = hash_refresh_token(f"member-scan:{user_id}")
        return self._cache_key(fingerprint=fingerprint, request=request)


class TrainerCheckInScanThrottle(_ConfiguredRateThrottle):
    """Limit trainer QR scans by gym membership and address, never by the payload."""

    scope = "trainer_check_in_scan"

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        membership = getattr(request, "gym_membership", None)
        membership_id = getattr(membership, "id", None)
        if not membership_id:
            return None
        fingerprint = hash_refresh_token(f"trainer-scan:{membership_id}")
        return self._cache_key(fingerprint=fingerprint, request=request)


class TrainerCheckInScanIPThrottle(_AddressBoundaryThrottle):
    """Bound trainer scan attempts from one address, including malformed ones."""

    scope = "trainer_check_in_scan_ip"
    boundary = "trainer-scan-ip"


class _MemberUserThrottle(_ConfiguredRateThrottle):
    """Limit a member route by the authenticated user, never by a selected member."""

    prefix = ""

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        user = getattr(request, "user", None)
        user_id = getattr(user, "pk", None)
        if not user_id:
            return None
        fingerprint = hash_refresh_token(f"{self.prefix}:{user_id}")
        return self._cache_key(fingerprint=fingerprint, request=request)


class MemberSelfReadThrottle(_MemberUserThrottle):
    scope = "member_self_read"
    prefix = "member-self-read"


class MemberSelfWriteThrottle(_MemberUserThrottle):
    scope = "member_self_write"
    prefix = "member-self-write"


class MemberFileWriteThrottle(_MemberUserThrottle):
    scope = "member_file_write"
    prefix = "member-file-write"


class MemberFileDownloadThrottle(_MemberUserThrottle):
    scope = "member_file_download"
    prefix = "member-file-download"


class MemberLoginIPThrottle(_AddressBoundaryThrottle):
    """Bound member login attempts from one address, including malformed ones."""

    scope = "member_login_ip"
    boundary = "member-login-ip"


class MemberLoginThrottle(_ConfiguredRateThrottle):
    """Limit member login attempts by address and normalized email hash."""

    scope = "member_login"

    def get_cache_key(self, request: Request, view: Any) -> str:
        email = request.data.get("email") if isinstance(request.data, dict) else ""
        fingerprint = hash_refresh_token(f"member-login:{str(email).strip().lower()}")
        return self._cache_key(fingerprint=fingerprint, request=request)


class MemberRefreshIPThrottle(_AddressBoundaryThrottle):
    """Bound member refresh attempts from one address, including malformed ones."""

    scope = "member_refresh_ip"
    boundary = "member-refresh-ip"


class MemberRefreshTokenThrottle(_PresentedTokenThrottle):
    """Limit member refresh by the presented token hash and address."""

    scope = "member_refresh"
    boundary = "member-refresh"


class MemberLogoutIPThrottle(_AddressBoundaryThrottle):
    """Bound member logout attempts from one address, including malformed ones."""

    scope = "member_logout_ip"
    boundary = "member-logout-ip"


class MemberLogoutTokenThrottle(_PresentedTokenThrottle):
    """Limit member logout by the presented token hash and address."""

    scope = "member_logout"
    boundary = "member-logout"


class PasswordForgotIPThrottle(_AddressBoundaryThrottle):
    """Bound forgot-password attempts from one address, including unknown emails."""

    scope = "password_forgot_ip"
    boundary = "password-forgot-ip"


class PasswordForgotThrottle(_ConfiguredRateThrottle):
    """Limit forgot-password attempts by address and normalized email hash."""

    scope = "password_forgot"

    def get_cache_key(self, request: Request, view: Any) -> str:
        email = request.data.get("email") if isinstance(request.data, dict) else ""
        fingerprint = hash_refresh_token(f"password-forgot:{str(email).strip().lower()}")
        return self._cache_key(fingerprint=fingerprint, request=request)


class PasswordResetIPThrottle(_AddressBoundaryThrottle):
    """Bound reset-password attempts from one address, including malformed tokens."""

    scope = "password_reset_ip"
    boundary = "password-reset-ip"


class PasswordResetThrottle(_ConfiguredRateThrottle):
    """Limit reset-password attempts by a peppered token hash and address."""

    scope = "password_reset"

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        token = request.data.get("reset_token") if isinstance(request.data, dict) else None
        if not isinstance(token, str) or not token:
            return None
        fingerprint = hash_refresh_token(f"password-reset:{token}")
        return self._cache_key(fingerprint=fingerprint, request=request)


class GymSelectionIssueThrottle(_ConfiguredRateThrottle):
    scope = "gym_selection_issue"

    def get_cache_key(self, request: Request, view: Any) -> str:
        email = request.data.get("email") if isinstance(request.data, dict) else ""
        self.normalized_email = str(email).strip().lower()
        fingerprint = hash_refresh_token(f"ticket-issue:{self.normalized_email}")
        return self._cache_key(fingerprint=fingerprint, request=request)

    def allow_request(self, request: Request, view: Any) -> bool:
        allowed = super().allow_request(request, view)
        if not allowed and self.normalized_email:
            try:
                user = User.objects.get(normalized_email=self.normalized_email)
            except User.DoesNotExist:
                return False
            write_platform_audit(
                actor=user,
                action="TICKET_ISSUE_RATE_LIMITED",
                target_type="User",
                target_id=user.id,
                request_id=getattr(request, "request_id", "req_unknown"),
                outcome=Outcome.DENIED,
                details={"reason": "RATE_LIMITED"},
            )
        return allowed


class GymSelectionThrottle(_ConfiguredRateThrottle):
    scope = "gym_selection_select"
    ticket: GymSelectionTicket | None = None

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        plaintext = (
            request.data.get("gym_selection_ticket") if isinstance(request.data, dict) else None
        )
        if not isinstance(plaintext, str) or not plaintext:
            return None
        fingerprint = hash_selection_ticket(plaintext)
        self.ticket = (
            GymSelectionTicket.objects.select_related("user")
            .filter(ticket_hash=fingerprint)
            .first()
        )
        return self._cache_key(fingerprint=fingerprint, request=request)

    def allow_request(self, request: Request, view: Any) -> bool:
        allowed = super().allow_request(request, view)
        if not allowed and self.ticket is not None:
            audit_ticket(
                user=self.ticket.user,
                ticket_id=self.ticket.id,
                action="TICKET_RATE_LIMITED",
                request_id=getattr(request, "request_id", "req_unknown"),
                outcome=Outcome.DENIED,
                reason="RATE_LIMITED",
            )
        return allowed
