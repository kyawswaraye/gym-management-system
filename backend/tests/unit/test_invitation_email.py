from __future__ import annotations

import pytest
from django.core import mail
from django.core.mail import send_mail

from apps.common.mail.invitations import render_invitation_email


def test_staff_invitation_email_is_branded_and_keeps_plain_text_token() -> None:
    mail = render_invitation_email(
        gym_name="Taunggyi Gym",
        acceptance_url="https://app.gympilotmm.com/accept-staff-invitation?token=abc123",
        role="staff",
        ttl_hours=48,
    )

    assert mail.subject == "You're invited to join Taunggyi Gym on GymPilot MM"
    assert "Taunggyi Gym" in mail.text
    assert "as staff" in mail.text
    assert "token=abc123" in mail.text
    assert "48 hours" in mail.text
    assert "<html" in mail.html.lower()
    assert "Accept invitation" in mail.html
    assert "Taunggyi Gym" in mail.html
    assert "https://app.gympilotmm.com/accept-staff-invitation?token=abc123" in mail.html
    assert "GymPilot MM" in mail.html


def test_owner_invitation_email_escapes_html_and_header_injection() -> None:
    mail = render_invitation_email(
        gym_name='East <script>alert(1)</script>\r\nBcc: evil@example.test Gym',
        acceptance_url="https://app.example.test/accept-owner-invitation?token=safe-token",
        role="owner",
        ttl_hours=24,
    )

    assert "\n" not in mail.subject
    assert "\r" not in mail.subject
    assert "East <script>alert(1)</script> Bcc: evil@example.test Gym" in mail.subject
    assert "&lt;script&gt;" in mail.html
    assert "<script>" not in mail.html
    assert "as an Owner" in mail.text
    assert "token=safe-token" in mail.text
    assert "24 hours" in mail.html


def test_invitation_email_rejects_non_http_urls() -> None:
    with pytest.raises(ValueError, match="absolute http"):
        render_invitation_email(
            gym_name="North Gym",
            acceptance_url="javascript:alert(1)",
            role="staff",
            ttl_hours=48,
        )


def test_send_mail_attaches_html_alternative_for_resend() -> None:
    invitation = render_invitation_email(
        gym_name="Taunggyi Gym",
        acceptance_url="https://app.gympilotmm.com/accept-staff-invitation?token=G62PlC",
        role="staff",
        ttl_hours=48,
    )
    mail.outbox.clear()
    send_mail(
        subject=invitation.subject,
        message=invitation.text,
        from_email="GymPilot MM <no-reply@gympilotmm.com>",
        recipient_list=["invitee@example.test"],
        fail_silently=False,
        html_message=invitation.html,
    )

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.subject.startswith("You're invited to join Taunggyi Gym")
    assert "token=G62PlC" in message.body
    assert message.alternatives[0][1] == "text/html"
    assert "Accept invitation" in message.alternatives[0][0]
