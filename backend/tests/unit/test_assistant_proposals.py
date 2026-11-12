"""Confirmation cards show the sale lines already stored on the proposal."""

from __future__ import annotations

from apps.assistant.proposals import public_pending

_STEP = "11111111-1111-1111-1111-111111111111"
_GOAL = "22222222-2222-2222-2222-222222222222"
_WATER = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_TOWEL = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _card(payload: dict[str, object], labels: dict[str, str] | None = None) -> dict[str, object]:
    card = public_pending(
        {
            "payload": payload,
            "labels": labels or {},
            "summary": "Create a retail sale draft.",
            "approval": None,
        },
        step_id=_STEP,
        goal_id=_GOAL,
        tool_name="create_retail_sale",
    )
    assert card is not None
    return card


def _values(card: dict[str, object]) -> dict[str, str]:
    fields = card["fields"]
    assert isinstance(fields, list)
    return {str(field["name"]): str(field["value"]) for field in fields}


def test_one_sale_line_shows_the_stored_name_sku_quantity_and_discount() -> None:
    card = _card(
        {
            "amount": "30.00",
            "lines": [
                {
                    "product_id": _WATER,
                    "quantity": "2",
                    "discount": "5.00",
                    "current_unit_cost": "10.00",
                    "margin": "15.00",
                }
            ],
        },
        {"lines.0.product_id": "Spring Water (WATER-B)"},
    )
    values = _values(card)

    assert values["lines.0.product"] == "Spring Water (WATER-B)"
    assert values["lines.0.quantity"] == "2"
    assert values["lines.0.discount"] == "5.00"
    assert values["amount"] == "30.00"
    assert _WATER not in values.values()
    assert "10.00" not in values.values()
    assert "15.00" not in values.values()


def test_several_sale_lines_keep_stored_order_and_skip_a_missing_discount() -> None:
    values = _values(
        _card(
            {
                "lines": [
                    {"product_id": _WATER, "quantity": "2", "discount": "1.00"},
                    {"product_id": _TOWEL, "quantity": "1"},
                ]
            },
            {
                "lines.0.product_id": "Spring Water (WATER-B)",
                "lines.1.product_id": "Gym Towel (TOWEL-1)",
            },
        )
    )

    assert values["lines.0.product"] == "Spring Water (WATER-B)"
    assert values["lines.0.quantity"] == "2"
    assert values["lines.0.discount"] == "1.00"
    assert values["lines.1.product"] == "Gym Towel (TOWEL-1)"
    assert values["lines.1.quantity"] == "1"
    assert "lines.1.discount" not in values


def test_a_line_without_a_stored_label_is_unavailable() -> None:
    values = _values(
        _card(
            {"lines": [{"product_id": _WATER, "quantity": "1"}]},
            {"lines.0.product_id": _WATER},
        )
    )

    assert values["lines.0.product"] == "Product unavailable"
    assert values["lines.0.quantity"] == "1"
    assert _WATER not in values["lines.0.product"]


def test_a_missing_line_does_not_add_line_fields() -> None:
    values = _values(_card({"business_date": "2026-11-11", "amount": "40.00"}))

    assert values == {"amount": "40.00", "business_date": "2026-11-11"}
