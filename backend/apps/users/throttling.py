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
