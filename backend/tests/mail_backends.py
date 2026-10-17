from __future__ import annotations

import time
from typing import ClassVar

from django.core.mail import EmailMessage
from django.core.mail.backends.base import BaseEmailBackend

from apps.notifications.services import IDEMPOTENCY_HEADER

PROVIDER_SECRET = "smtp failed for member@secret.example password=hunter2"


class ProviderOutageBackend(BaseEmailBackend):
    def send_messages(self, email_messages: list[EmailMessage]) -> int:
        raise ConnectionError(PROVIDER_SECRET)


class ZeroSendBackend(BaseEmailBackend):
    def send_messages(self, email_messages: list[EmailMessage]) -> int:
        return 0


class SlowBackend(BaseEmailBackend):
    fail_silently_values: ClassVar[list[bool]] = []
    sent_keys: ClassVar[list[str]] = []

    def __init__(self, *args: object, fail_silently: bool = False, **kwargs: object) -> None:
        super().__init__(fail_silently=fail_silently)
        SlowBackend.fail_silently_values.append(fail_silently)

    def send_messages(self, email_messages: list[EmailMessage]) -> int:
        time.sleep(0.4)
        for message in email_messages:
            SlowBackend.sent_keys.append(str(message.extra_headers.get(IDEMPOTENCY_HEADER, "")))
        return len(email_messages)
