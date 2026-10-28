"""Branded invitation emails with plain-text bodies and HTML alternatives."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from typing import Literal

RoleKind = Literal["staff", "owner"]

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_PRODUCT_NAME = "GymPilot MM"
_BRAND_TEAL = "#196862"
_BRAND_TEAL_DARK = "#0e3936"
_BRAND_SURFACE = "#f2f7f7"
_BRAND_BORDER = "#c4dfdc"
_TEXT = "#122538"
_MUTED = "#5c6778"
_WHITE = "#ffffff"


@dataclass(frozen=True, slots=True)
class InvitationEmail:
    subject: str
    text: str
    html: str


def render_invitation_email(
    *,
    gym_name: str,
    acceptance_url: str,
    role: RoleKind,
    ttl_hours: int,
) -> InvitationEmail:
    """Build a safe subject, plain-text body, and HTML alternative.

    The plain-text body always includes ``token=`` so existing acceptance
    flows and tests can keep reading the link from ``message.body``. User-
    controlled gym names are collapsed for the subject and HTML-escaped in
    the markup.
    """

    safe_gym = _header_safe(gym_name, max_length=150) or "your gym"
    safe_url = _header_safe(acceptance_url, max_length=2048)
    if not safe_url.startswith(("https://", "http://")):
        raise ValueError("acceptance URL must be an absolute http(s) link")
    hours = max(1, int(ttl_hours))
    role_label = "an Owner" if role == "owner" else "staff"
    subject = _header_safe(
        f"You're invited to join {safe_gym} on {_PRODUCT_NAME}",
        max_length=200,
    )
    text = _plain_text(
        gym_name=safe_gym,
        acceptance_url=safe_url,
        role_label=role_label,
        hours=hours,
    )
    html = _html(
        gym_name=safe_gym,
        acceptance_url=safe_url,
        role=role,
        role_label=role_label,
        hours=hours,
    )
    return InvitationEmail(subject=subject, text=text, html=html)


def _header_safe(value: str, *, max_length: int) -> str:
    without_controls = _CONTROL_RE.sub(" ", value)
    collapsed = " ".join(without_controls.split())
    return collapsed[:max_length].strip()


def _plain_text(
    *,
    gym_name: str,
    acceptance_url: str,
    role_label: str,
    hours: int,
) -> str:
    return (
        f"You were invited to join {gym_name} as {role_label} on {_PRODUCT_NAME}.\n"
        f"\n"
        f"Accept the invitation:\n"
        f"{acceptance_url}\n"
        f"\n"
        f"This link expires in {hours} hours and can be used once.\n"
        f"If you were not expecting this invitation, you can ignore this email.\n"
        f"\n"
        f"— {_PRODUCT_NAME}\n"
    )


def _html(
    *,
    gym_name: str,
    acceptance_url: str,
    role: RoleKind,
    role_label: str,
    hours: int,
) -> str:
    gym = escape(gym_name, quote=True)
    url = escape(acceptance_url, quote=True)
    product = escape(_PRODUCT_NAME, quote=True)
    role_phrase = "Owner" if role == "owner" else "staff member"
    intro = (
        f'You have been invited to join <strong style="color:{_BRAND_TEAL_DARK};">{gym}</strong> '
        f"as {escape(role_phrase, quote=True)}."
    )
    body_font = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
    card_style = (
        f"max-width:520px;background:{_WHITE};border:1px solid {_BRAND_BORDER};"
        f"border-radius:16px;overflow:hidden;"
    )
    brand_mark = (
        "margin:0;font-size:13px;letter-spacing:0.08em;text-transform:uppercase;color:#d1f0dc;"
    )
    title_style = f"margin:8px 0 0;font-size:24px;line-height:1.25;font-weight:700;color:{_WHITE};"
    button_link = (
        f"display:inline-block;padding:14px 28px;font-size:16px;"
        f"font-weight:600;color:{_WHITE};text-decoration:none;"
    )
    expiry = (
        f"This link expires in {hours} hours and can be used once. "
        "If you were not expecting this invitation, you can ignore this email."
    )
    footer_cell = (
        f"padding:18px 32px;border-top:1px solid {_BRAND_BORDER};background:{_BRAND_SURFACE};"
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(f"Invitation to {gym_name}", quote=True)}</title>
</head>
<body style="margin:0;padding:0;background:{_BRAND_SURFACE};color:{_TEXT};font-family:{body_font};">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"
  style="background:{_BRAND_SURFACE};padding:32px 16px;">
  <tr>
    <td align="center">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
        style="{card_style}">
        <tr>
          <td style="background:{_BRAND_TEAL};padding:28px 32px;">
            <p style="{brand_mark}">{product}</p>
            <h1 style="{title_style}">You're invited</h1>
          </td>
        </tr>
        <tr>
          <td style="padding:32px;">
            <p style="margin:0 0 16px;font-size:16px;line-height:1.55;color:{_TEXT};">{intro}</p>
            <p style="margin:0 0 28px;font-size:15px;line-height:1.55;color:{_MUTED};">
              Accept below to set up your account and start working with the gym team.
            </p>
            <table role="presentation" cellspacing="0" cellpadding="0"
              style="margin:0 auto 28px;">
              <tr>
                <td style="border-radius:10px;background:{_BRAND_TEAL};">
                  <a href="{url}" style="{button_link}">
                    Accept invitation
                  </a>
                </td>
              </tr>
            </table>
            <p style="margin:0 0 8px;font-size:13px;line-height:1.5;color:{_MUTED};">
              Or copy this link into your browser:
            </p>
            <p style="margin:0 0 24px;font-size:13px;line-height:1.5;word-break:break-all;">
              <a href="{url}" style="color:{_BRAND_TEAL};text-decoration:underline;">{url}</a>
            </p>
            <p style="margin:0;font-size:13px;line-height:1.5;color:{_MUTED};">
              {expiry}
            </p>
          </td>
        </tr>
        <tr>
          <td style="{footer_cell}">
            <p style="margin:0;font-size:12px;line-height:1.5;color:{_MUTED};">
              Sent by {product} · Staff workspace for {gym}
            </p>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>
"""
