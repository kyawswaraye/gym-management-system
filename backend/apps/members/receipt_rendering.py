"""Deterministic printable receipt HTML. The document is not stored."""

from __future__ import annotations

from html import escape

from django.http import HttpResponse

from apps.members.models import Receipt

_CSP = "default-src 'none'; sandbox; base-uri 'none'; form-action 'none'"


def render_receipt_html(receipt: Receipt) -> str:
    rows = (
        ("Receipt", receipt.receipt_number),
        ("Issued", receipt.issued_at.isoformat()),
        ("Gym", receipt.gym_code),
        ("Gym name", receipt.gym_name),
        ("Address", receipt.gym_address),
        ("Member number", receipt.member_number),
        ("Member", receipt.member_name),
        ("Description", receipt.description),
        ("Amount", f"{receipt.amount} {receipt.currency}"),
        ("Method", receipt.method),
        ("Business date", receipt.business_date.isoformat()),
        ("Issuer", receipt.issuer_name),
        ("Status", receipt.status),
    )
    body = "".join(
        f"<tr><th>{escape(label)}</th><td>{escape(str(value))}</td></tr>" for label, value in rows
    )
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{escape(_CSP)}">'
        "<title>Internal receipt</title></head><body>"
        "<h1>Internal receipt — not a tax invoice</h1>"
        f"<table>{body}</table></body></html>"
    )


def receipt_download_response(receipt: Receipt) -> HttpResponse:
    filename = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in receipt.receipt_number
    )
    response = HttpResponse(render_receipt_html(receipt), content_type="text/html; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}.html"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = _CSP
    return response
