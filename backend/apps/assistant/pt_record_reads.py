"""PT sale and booking reads. These are posted or stored records, not the catalog.

search_service_packages is the sellable catalog. search_pt_packages is a granted
package and its remaining sessions. This module reads the sale and the booking.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Count, Sum
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import ExecutionMode, ReadCall, ToolContract, execute_read_tool
from apps.assistant.prerequisites import FieldRequirement, Prerequisite
from apps.assistant.registry import ToolDefinition
from apps.members.payment_serializers import money_text
from apps.trainers.models import PTBooking, PTSale
from apps.trainers.selectors import page_pt_bookings, page_pt_sales
from apps.users.serializers import StrictSerializer

SEARCH_PT_SALES = "search_pt_sales"
SEARCH_PT_BOOKINGS = "search_pt_bookings"
SEARCH_PT_TRAINER_SALES = "search_pt_trainer_sales"
PT_RECORD_READ_TOOL_NAMES = (SEARCH_PT_SALES, SEARCH_PT_BOOKINGS, SEARCH_PT_TRAINER_SALES)


class PTSaleReadQuerySerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=PTSale.Status.choices, required=False)
    member_id = serializers.UUIDField(required=False)
    trainer_staff_profile_id = serializers.UUIDField(required=False)
    service_date_from = serializers.DateField(required=False)
    service_date_to = serializers.DateField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=4096)


class PTBookingReadQuerySerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=PTBooking.Status.choices, required=False)
    member_id = serializers.UUIDField(required=False)
    trainer_staff_profile_id = serializers.UUIDField(required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=4096)


def pt_record_read_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.definition for tool in _READS.values())


def pt_record_read_tool_contracts() -> dict[str, ToolContract]:
    return {name: tool for name, tool in _READS.items()}


def execute_registered_read(
    *,
    request: Request,
    view: APIView,
    tool_name: str,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    contract = _READS.get(tool_name)
    if contract is None:
        raise ExecutionRejected("This tool is not registered.", code="unknown_tool")
    return execute_read_tool(
        request=request,
        view=view,
        contract=contract,
        payload=payload,
        read=_READERS[tool_name],
    )


def search_pt_sales(call: ReadCall) -> dict[str, object]:
    if call.gym.id is None or call.actor.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    query = _sale_query(call.payload)
    member_id = query.get("member_id")
    trainer_id = query.get("trainer_staff_profile_id")
    rows, meta = page_pt_sales(
        gym=call.gym,
        actor=call.actor,
        status=str(query.get("status") or ""),
        member_id=member_id if isinstance(member_id, UUID) else None,
        trainer_staff_profile_id=trainer_id if isinstance(trainer_id, UUID) else None,
        service_date_from=_day(query.get("service_date_from")),
        service_date_to=_day(query.get("service_date_to")),
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    return _page(rows, meta, _sale_row)


def search_pt_bookings(call: ReadCall) -> dict[str, object]:
    if call.gym.id is None or call.actor.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    query = _booking_query(call.payload)
    member_id = query.get("member_id")
    trainer_id = query.get("trainer_staff_profile_id")
    rows, meta = page_pt_bookings(
        gym=call.gym,
        actor=call.actor,
        status=str(query.get("status") or ""),
        trainer_staff_profile_id=trainer_id if isinstance(trainer_id, UUID) else None,
        member_id=member_id if isinstance(member_id, UUID) else None,
        date_from=_day(query.get("date_from")),
        date_to=_day(query.get("date_to")),
        view="",
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    return _page(rows, meta, _booking_row)


def _page(rows: list[Any], meta: Mapping[str, Any], render: Any) -> dict[str, object]:
    return {
        "results": [render(row) for row in rows],
        "has_more": bool(meta["has_more"]),
        "next_after": meta["next_after"],
    }


def _sale_row(row: PTSale) -> dict[str, object]:
    return {
        "id": str(row.id),
        "member_id": str(row.member_id),
        "trainer_staff_profile_id": str(row.trainer_staff_profile_id),
        "service_date": row.service_date.isoformat(),
        "status": row.status,
        "gross_fee": money_text(row.gross_fee),
        "currency": row.currency,
    }


def _booking_row(row: PTBooking) -> dict[str, object]:
    return {
        "id": str(row.id),
        "member_id": str(row.member_id),
        "trainer_staff_profile_id": str(row.trainer_staff_profile_id),
        "starts_at": row.starts_at.isoformat(),
        "status": row.status,
    }


def _day(value: object) -> date | None:
    return value if isinstance(value, date) else None


def _definition(name: str, description: str, permission: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        module="trainers",
        operation="read",
        permissions=(permission,),
        features=("pt",),
    )


def _contract(definition: ToolDefinition, fields: tuple[FieldRequirement, ...]) -> ToolContract:
    return ToolContract(
        definition=definition,
        mode=ExecutionMode.READ,
        prerequisite=Prerequisite(
            code=definition.name,
            description=definition.description,
            fields=fields,
        ),
    )


def _sale_query(payload: Mapping[str, object]) -> dict[str, Any]:
    serializer = PTSaleReadQuerySerializer(data=_without_gym(payload))
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


class PTTrainerSaleQuerySerializer(StrictSerializer):
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        start = attrs.get("date_from")
        end = attrs.get("date_to")
        if isinstance(start, date) and isinstance(end, date) and end < start:
            raise serializers.ValidationError({"date_to": ["Must be on or after date_from."]})
        return attrs


def search_pt_trainer_sales(call: ReadCall) -> dict[str, object]:
    """Rank posted PT sales by trainer. Drafts and voids are not sales."""

    if call.gym.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    serializer = PTTrainerSaleQuerySerializer(data=_without_gym(call.payload))
    serializer.is_valid(raise_exception=True)
    query = serializer.validated_data
    queryset = PTSale.objects.filter(
        gym_id=call.gym.id,
        member__gym_id=call.gym.id,
        trainer_staff_profile__gym_id=call.gym.id,
        status=PTSale.Status.POSTED,
    )
    start = query.get("date_from")
    end = query.get("date_to")
    if isinstance(start, date):
        queryset = queryset.filter(service_date__gte=start)
    if isinstance(end, date):
        queryset = queryset.filter(service_date__lte=end)
    grouped = (
        queryset.values("trainer_staff_profile_id", "trainer_staff_profile__display_name")
        .annotate(
            posted_sale_count=Count("id"),
            posted_gross_fee=Sum("gross_fee"),
        )
        .order_by("-posted_gross_fee", "trainer_staff_profile__display_name")[: int(query["limit"])]
    )
    return {
        "results": [
            {
                "trainer_staff_profile_id": str(row["trainer_staff_profile_id"]),
                "trainer_name": row["trainer_staff_profile__display_name"],
                "posted_sale_count": int(row["posted_sale_count"]),
                "posted_gross_fee": money_text(row["posted_gross_fee"] or Decimal("0")),
            }
            for row in grouped
        ],
        "has_more": False,
        "next_after": None,
    }


def _booking_query(payload: Mapping[str, object]) -> dict[str, Any]:
    serializer = PTBookingReadQuerySerializer(data=_without_gym(payload))
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


def _without_gym(payload: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key != "gym_id"}


_PAGE = (
    FieldRequirement("limit", "Page size from 1 to 25. Optional.", required=False, kind="integer"),
    FieldRequirement("after", "Cursor from the previous page. Optional.", required=False),
)
_READS = {
    SEARCH_PT_SALES: _contract(
        _definition(
            SEARCH_PT_SALES,
            "Search PT sales in this gym. This is a completed or draft sale, not the "
            "sellable package catalog and not the remaining-session balance.",
            "pt.sales.view",
        ),
        (
            FieldRequirement("status", "Stored sale status. Optional.", required=False),
            FieldRequirement("member_id", "Member id. Optional.", required=False),
            FieldRequirement(
                "trainer_staff_profile_id",
                "Trainer staff profile id. Optional.",
                required=False,
            ),
            FieldRequirement("service_date_from", "First service date. Optional.", required=False),
            FieldRequirement("service_date_to", "Last service date. Optional.", required=False),
            *_PAGE,
        ),
    ),
    SEARCH_PT_BOOKINGS: _contract(
        _definition(
            SEARCH_PT_BOOKINGS,
            "Search PT bookings in this gym. A booking is one scheduled session, "
            "not a sale and not the remaining-session balance.",
            "pt.schedule.view",
        ),
        (
            FieldRequirement("status", "Stored booking status. Optional.", required=False),
            FieldRequirement("member_id", "Member id. Optional.", required=False),
            FieldRequirement(
                "trainer_staff_profile_id",
                "Trainer staff profile id. Optional.",
                required=False,
            ),
            FieldRequirement("date_from", "First session date. Optional.", required=False),
            FieldRequirement("date_to", "Last session date. Optional.", required=False),
            *_PAGE,
        ),
    ),
    SEARCH_PT_TRAINER_SALES: _contract(
        _definition(
            SEARCH_PT_TRAINER_SALES,
            "Rank this gym's posted PT sales by trainer. Each row is one trainer's "
            "posted sale count and the sum of stored gross fees. Drafts, rejected "
            "sales, and voids are excluded. Compare trainers from this result. "
            "Do not guess a ranking.",
            "pt.sales.view",
        ),
        (
            FieldRequirement("date_from", "First service date. Optional.", required=False),
            FieldRequirement("date_to", "Last service date. Optional.", required=False),
            FieldRequirement(
                "limit",
                "Page size from 1 to 25. Optional.",
                required=False,
                kind="integer",
            ),
        ),
    ),
}
_READERS = {
    SEARCH_PT_SALES: search_pt_sales,
    SEARCH_PT_BOOKINGS: search_pt_bookings,
    SEARCH_PT_TRAINER_SALES: search_pt_trainer_sales,
}
