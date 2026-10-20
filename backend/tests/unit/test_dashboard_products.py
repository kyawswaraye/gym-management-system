"""Dashboard product ranking keeps a fixed top list."""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from apps.reports.selectors import TOP_PRODUCT_LIMIT, _products

GYM_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
START = date(2026, 10, 1)
END = date(2026, 10, 31)


def _row(product_id: UUID, name: str, revenue: str, profit: str) -> dict[str, object]:
    return {
        "product_id": product_id,
        "name": name,
        "quantity": Decimal("1.000"),
        "revenue": Decimal(revenue),
        "profit": Decimal(profit),
    }


def test_top_products_return_ten_in_rank_order(monkeypatch) -> None:
    assert TOP_PRODUCT_LIMIT == 10
    leaders = [
        _row(UUID(int=index), f"Item {index:02d}", f"{30 - index}.00", "2.00")
        for index in range(1, 10)
    ]
    tied_keep = _row(
        UUID("00000000-0000-4000-8000-000000000010"),
        "Tied high profit",
        "10.00",
        "5.00",
    )
    tied_drop = _row(
        UUID("00000000-0000-4000-8000-000000000011"),
        "Tied low profit",
        "10.00",
        "1.00",
    )
    zero = _row(uuid4(), "Cancelled out", "0.00", "0.00")
    calls: list[bool] = []

    def sale_rows(**kwargs: object) -> list[dict[str, object]]:
        assert kwargs["gym_id"] == GYM_ID
        assert kwargs["date_from"] == START
        assert kwargs["date_to"] == END
        calls.append(bool(kwargs["correction"]))
        if kwargs["correction"]:
            return []
        return [*leaders, tied_drop, tied_keep, zero]

    monkeypatch.setattr("apps.reports.selectors._sale_product_rows", sale_rows)
    monkeypatch.setattr(
        "apps.reports.selectors._return_product_rows",
        lambda **kwargs: [],
    )

    ranked = _products(gym_id=GYM_ID, date_from=START, date_to=END)

    assert calls == [False, True]
    assert len(ranked) == TOP_PRODUCT_LIMIT
    assert [item["revenue"] for item in ranked] == [
        "29.00",
        "28.00",
        "27.00",
        "26.00",
        "25.00",
        "24.00",
        "23.00",
        "22.00",
        "21.00",
        "10.00",
    ]
    assert ranked[-1]["product_id"] == tied_keep["product_id"]
    assert ranked[-1]["name"] == "Tied high profit"
    dropped = {tied_drop["product_id"], zero["product_id"]}
    assert dropped.isdisjoint(item["product_id"] for item in ranked)
