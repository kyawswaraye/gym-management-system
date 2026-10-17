from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.members.models import Member, Membership, MembershipPlan, Payment
from apps.members.serializers import (
    MemberArchiveSerializer,
    MemberCreateSerializer,
    MemberEnvelopeSerializer,
    MemberListEnvelopeSerializer,
    MemberListQuerySerializer,
    MemberSerializer,
    MembershipCancelSerializer,
    MembershipCommandSerializer,
    MembershipCreateSerializer,
    MembershipEnvelopeSerializer,
    MembershipExpiryQuerySerializer,
    MembershipListEnvelopeSerializer,
    MembershipListQuerySerializer,
    MembershipPlanArchiveSerializer,
    MembershipPlanCreateSerializer,
    MembershipPlanEnvelopeSerializer,
    MembershipPlanListEnvelopeSerializer,
    MembershipPlanListQuerySerializer,
    MembershipPlanSerializer,
    MembershipPlanUpdateSerializer,
    MembershipSerializer,
    MembershipUpdateSerializer,
    MemberSummaryEnvelopeSerializer,
    MemberSummaryQuerySerializer,
    MemberSummarySerializer,
    MemberUpdateSerializer,
    PaymentCreateSerializer,
    PaymentSerializer,
    ReasonSerializer,
    RefundSerializer,
)
from apps.members.services import (
    approve_payment,
    archive_member,
    archive_plan,
    cancel_membership,
    create_member,
    create_membership,
    create_plan,
    gym_today,
    member_summary,
    page_members,
    page_membership_expiry,
    page_membership_plans,
    page_memberships,
    refund_payment,
    resume_membership,
    submit_payment,
    suspend_membership,
    update_member,
    update_membership_note,
    update_plan,
    void_payment,
)
from apps.users.permissions import RequiresEntitlement, RequiresPermission


class TenantAPIView(APIView):
    permission_classes = [IsAuthenticated, RequiresPermission, RequiresEntitlement]


class MemberFeatureAPIView(TenantAPIView):
    required_feature = "members"


class MembershipFeatureAPIView(TenantAPIView):
    required_feature = "memberships"


class FinanceFeatureAPIView(TenantAPIView):
    required_feature = "finance"


class ReportsFeatureAPIView(TenantAPIView):
    required_feature = "reports"


class MemberListCreateView(MemberFeatureAPIView):
    required_permission = "members.view"

    def get_permissions(self):
        self.required_permission = (
            "members.create" if self.request.method == "POST" else "members.view"
        )
        return super().get_permissions()

    @extend_schema(
        operation_id="members_list",
        parameters=[MemberListQuerySerializer],
        responses={
            200: MemberListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = MemberListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        filters = query.validated_data
        members, meta = page_members(
            Member.objects.filter(gym=request.gym),
            gym_id=request.gym.id,
            status=str(filters.get("status", "")),
            search=str(filters.get("search", "")),
            joined_from=filters.get("joined_from"),
            joined_to=filters.get("joined_to"),
            is_pt_client=filters.get("is_pt_client"),
            limit=int(filters["limit"]),
            after=str(filters.get("after", "")),
        )
        return success_response(MemberSerializer(members, many=True).data, meta=meta)

    @extend_schema(
        operation_id="members_create",
        request=MemberCreateSerializer,
        responses={
            201: MemberEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        serializer = MemberCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        member = create_member(
            gym=request.gym,
            actor=request.gym_membership,
            member_number=str(payload.get("member_number", "")),
            first_name=str(payload["first_name"]),
            last_name=str(payload["last_name"]),
            phone=payload.get("phone"),
            email=payload.get("email"),
            gender=payload.get("gender"),
            date_of_birth=payload.get("date_of_birth"),
            address=dict(payload.get("address") or {}),
            join_date=payload["join_date"],
            emergency_contact_name=str(payload.get("emergency_contact_name", "")),
            emergency_contact_phone=str(payload.get("emergency_contact_phone", "")),
            notes=str(payload.get("notes", "")),
            profile_photo_asset_id=payload.get("profile_photo_asset_id"),
        )
        return success_response(
            MemberSerializer(member).data,
            message="Member created",
            status=status.HTTP_201_CREATED,
        )


class MemberDetailView(MemberFeatureAPIView):
    required_permission = "members.view"

    def get_permissions(self):
        if self.request.method == "PATCH":
            self.required_permission = "members.update"
        return super().get_permissions()

    def get_object(self) -> Member:
        return get_object_or_404(Member, gym=self.request.gym, id=self.kwargs["id"])

    @extend_schema(
        operation_id="members_retrieve",
        responses={
            200: MemberEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, id: UUID) -> Response:
        return success_response(MemberSerializer(self.get_object()).data)

    @extend_schema(
        operation_id="members_update",
        request=MemberUpdateSerializer,
        responses={
            200: MemberEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def patch(self, request: Request, id: UUID) -> Response:
        serializer = MemberUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = update_member(
            gym=request.gym,
            actor=request.gym_membership,
            member_id=id,
            changes=dict(serializer.validated_data),
        )
        return success_response(MemberSerializer(member).data, message="Member updated")


class MemberArchiveView(MemberFeatureAPIView):
    required_permission = "members.archive"

    @extend_schema(
        operation_id="members_archive",
        request=MemberArchiveSerializer,
        responses={
            200: MemberEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> Response:
        MemberArchiveSerializer(data=request.data).is_valid(raise_exception=True)
        member = archive_member(gym=request.gym, actor=request.gym_membership, member_id=id)
        return success_response(MemberSerializer(member).data, message="Member archived")


class MemberSummaryView(MemberFeatureAPIView):
    required_permission = "members.view"

    @extend_schema(
        operation_id="members_summary",
        responses={
            200: MemberSummaryEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, id: UUID) -> Response:
        MemberSummaryQuerySerializer(data=request.query_params).is_valid(raise_exception=True)
        member = get_object_or_404(Member, gym=request.gym, id=id)
        payload = member_summary(gym=request.gym, member=member)
        return success_response(MemberSummarySerializer(payload).data)


class PlanListCreateView(MembershipFeatureAPIView):
    required_permission = "memberships.view"

    def get_permissions(self):
        self.required_permission = (
            "memberships.create" if self.request.method == "POST" else "memberships.view"
        )
        return super().get_permissions()

    @extend_schema(
        operation_id="membership_plans_list",
        parameters=[MembershipPlanListQuerySerializer],
        responses={
            200: MembershipPlanListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = MembershipPlanListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        filters = query.validated_data
        plans, meta = page_membership_plans(
            MembershipPlan.objects.filter(gym=request.gym),
            gym_id=request.gym.id,
            limit=int(filters["limit"]),
            after=str(filters.get("after", "")),
        )
        return success_response(MembershipPlanSerializer(plans, many=True).data, meta=meta)

    @extend_schema(
        operation_id="membership_plans_create",
        request=MembershipPlanCreateSerializer,
        responses={
            201: MembershipPlanEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        serializer = MembershipPlanCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        plan = create_plan(
            gym=request.gym,
            actor=request.gym_membership,
            code=str(payload["code"]),
            name=str(payload["name"]),
            description=str(payload.get("description", "")),
            duration_value=int(payload["duration_value"]),
            duration_unit=str(payload["duration_unit"]),
            price=payload["price"],
        )
        return success_response(
            MembershipPlanSerializer(plan).data,
            message="Membership plan created",
            status=status.HTTP_201_CREATED,
        )


class PlanDetailView(MembershipFeatureAPIView):
    required_permission = "memberships.view"

    def get_permissions(self):
        if self.request.method == "PATCH":
            self.required_permission = "memberships.update"
        return super().get_permissions()

    def get_object(self) -> MembershipPlan:
        return get_object_or_404(MembershipPlan, gym=self.request.gym, id=self.kwargs["id"])

    @extend_schema(
        operation_id="membership_plans_retrieve",
        responses={
            200: MembershipPlanEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, id: UUID) -> Response:
        return success_response(MembershipPlanSerializer(self.get_object()).data)

    @extend_schema(
        operation_id="membership_plans_update",
        request=MembershipPlanUpdateSerializer,
        responses={
            200: MembershipPlanEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def patch(self, request: Request, id: UUID) -> Response:
        serializer = MembershipPlanUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan = update_plan(
            gym=request.gym,
            actor=request.gym_membership,
            plan_id=id,
            changes=dict(serializer.validated_data),
        )
        return success_response(
            MembershipPlanSerializer(plan).data, message="Membership plan updated"
        )


class PlanArchiveView(MembershipFeatureAPIView):
    required_permission = "memberships.update"

    @extend_schema(
        operation_id="membership_plans_archive",
        request=MembershipPlanArchiveSerializer,
        responses={
            200: MembershipPlanEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> Response:
        MembershipPlanArchiveSerializer(data=request.data).is_valid(raise_exception=True)
        plan = archive_plan(gym=request.gym, actor=request.gym_membership, plan_id=id)
        return success_response(
            MembershipPlanSerializer(plan).data, message="Membership plan archived"
        )


class MembershipListCreateView(MembershipFeatureAPIView):
    required_permission = "memberships.view"

    def get_permissions(self):
        self.required_permission = (
            "memberships.create" if self.request.method == "POST" else "memberships.view"
        )
        return super().get_permissions()

    @extend_schema(
        operation_id="memberships_list",
        parameters=[MembershipListQuerySerializer],
        responses={
            200: MembershipListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = MembershipListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        filters = query.validated_data
        member_id = filters.get("member_id")
        if member_id is not None:
            get_object_or_404(Member, gym=request.gym, id=member_id)
        memberships, meta = page_memberships(
            Membership.objects.filter(gym=request.gym),
            gym_id=request.gym.id,
            status=str(filters.get("status", "")),
            member_id=member_id,
            payment_status=str(filters.get("payment_status", "")),
            limit=int(filters["limit"]),
            after=str(filters.get("after", "")),
        )
        return success_response(MembershipSerializer(memberships, many=True).data, meta=meta)

    @extend_schema(
        operation_id="memberships_create",
        request=MembershipCreateSerializer,
        responses={
            201: MembershipEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        serializer = MembershipCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        member = get_object_or_404(Member, gym=request.gym, id=payload["member_id"])
        plan = get_object_or_404(MembershipPlan, gym=request.gym, id=payload["plan_id"])
        membership = create_membership(
            gym=request.gym,
            actor=request.gym_membership,
            member=member,
            plan=plan,
            start_date=payload["start_date"],
            discount_amount=payload["discount_amount"],
            note=str(payload.get("note", "")),
        )
        return success_response(
            MembershipSerializer(membership).data,
            message="Membership created",
            status=status.HTTP_201_CREATED,
        )


class MembershipDetailView(MembershipFeatureAPIView):
    required_permission = "memberships.view"

    def get_permissions(self):
        if self.request.method == "PATCH":
            self.required_permission = "memberships.update"
        return super().get_permissions()

    def get_object(self) -> Membership:
        return get_object_or_404(Membership, gym=self.request.gym, id=self.kwargs["id"])

    @extend_schema(
        operation_id="memberships_retrieve",
        responses={
            200: MembershipEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, id: UUID) -> Response:
        return success_response(MembershipSerializer(self.get_object()).data)

    @extend_schema(
        operation_id="memberships_update",
        request=MembershipUpdateSerializer,
        responses={
            200: MembershipEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def patch(self, request: Request, id: UUID) -> Response:
        serializer = MembershipUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = update_membership_note(
            gym=request.gym,
            membership_id=id,
            note=str(serializer.validated_data["note"]),
        )
        return success_response(MembershipSerializer(membership).data, message="Membership updated")


class MembershipCancelView(MembershipFeatureAPIView):
    required_permission = "memberships.cancel"

    @extend_schema(
        operation_id="memberships_cancel",
        request=MembershipCancelSerializer,
        responses={
            200: MembershipEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> Response:
        serializer = MembershipCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        get_object_or_404(Membership, gym=request.gym, id=id)
        membership = cancel_membership(
            gym=request.gym,
            actor=request.gym_membership,
            membership_id=id,
            reason=str(serializer.validated_data["reason"]),
        )
        return success_response(
            MembershipSerializer(membership).data, message="Membership cancelled"
        )


class MembershipSuspendView(MembershipFeatureAPIView):
    required_permission = "memberships.update"

    @extend_schema(
        operation_id="memberships_suspend",
        request=MembershipCommandSerializer,
        responses={
            200: MembershipEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> Response:
        MembershipCommandSerializer(data=request.data).is_valid(raise_exception=True)
        get_object_or_404(Membership, gym=request.gym, id=id)
        membership = suspend_membership(gym=request.gym, membership_id=id)
        return success_response(
            MembershipSerializer(membership).data, message="Membership suspended"
        )


class MembershipResumeView(MembershipFeatureAPIView):
    required_permission = "memberships.update"

    @extend_schema(
        operation_id="memberships_resume",
        request=MembershipCommandSerializer,
        responses={
            200: MembershipEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> Response:
        MembershipCommandSerializer(data=request.data).is_valid(raise_exception=True)
        get_object_or_404(Membership, gym=request.gym, id=id)
        membership = resume_membership(gym=request.gym, membership_id=id)
        return success_response(MembershipSerializer(membership).data, message="Membership resumed")


class MembershipExpiryView(MembershipFeatureAPIView):
    required_permission = "memberships.view"

    @extend_schema(
        operation_id="memberships_expiry",
        parameters=[MembershipExpiryQuerySerializer],
        responses={
            200: MembershipListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = MembershipExpiryQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        filters = query.validated_data
        memberships, meta = page_membership_expiry(
            Membership.objects.filter(gym=request.gym),
            gym_id=request.gym.id,
            bucket=str(filters["bucket"]),
            today=gym_today(request.gym),
            limit=int(filters["limit"]),
            after=str(filters.get("after", "")),
        )
        return success_response(MembershipSerializer(memberships, many=True).data, meta=meta)


class PaymentListCreateView(FinanceFeatureAPIView):
    required_permission = "payments.view"

    def get_permissions(self):
        self.required_permission = (
            "payments.create" if self.request.method == "POST" else "payments.view"
        )
        return super().get_permissions()

    def get(self, request):
        payments = Payment.objects.filter(gym=request.gym).select_related(
            "member",
            "membership",
            "receipt",
        )
        if value := request.query_params.get("status"):
            payments = payments.filter(status=value)
        return success_response(
            PaymentSerializer(payments.order_by("-received_at")[:200], many=True).data
        )

    def post(self, request):
        serializer = PaymentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = get_object_or_404(
            Member, gym=request.gym, id=serializer.validated_data.pop("member_id")
        )
        membership_id = serializer.validated_data.pop("membership_id", None)
        membership = None
        if membership_id:
            membership = get_object_or_404(
                Membership, gym=request.gym, member=member, id=membership_id
            )
        if serializer.validated_data["currency"] != request.gym.currency:
            from rest_framework.exceptions import ValidationError

            raise ValidationError({"currency": ["Currency must match the selected gym."]})
        payment = Payment.objects.create(
            gym=request.gym,
            member=member,
            membership=membership,
            created_by_membership_id=request.gym_membership.id,
            updated_by_membership_id=request.gym_membership.id,
            **serializer.validated_data,
        )
        return success_response(
            PaymentSerializer(payment).data,
            message="Payment draft created",
            status=status.HTTP_201_CREATED,
        )


class PaymentActionView(FinanceFeatureAPIView):
    action_permissions = {
        "submit": "payments.submit",
        "approve": "payments.approve",
        "reject": "payments.approve",
        "cancel": "payments.update",
        "void": "payments.void",
        "refund": "payments.refund",
    }

    @property
    def required_permission(self):
        return self.action_permissions.get(self.kwargs.get("action"), "payments.view")

    @transaction.atomic
    def post(self, request, payment_id, action):
        payment = get_object_or_404(Payment, gym=request.gym, id=payment_id)
        if action == "submit":
            payment = submit_payment(payment=payment, actor=request.gym_membership)
        elif action == "approve":
            payment, receipt = approve_payment(payment=payment, actor=request.gym_membership)
            return success_response(
                {
                    "payment": PaymentSerializer(payment).data,
                    "receipt_number": receipt.receipt_number,
                },
                message="Payment approved",
            )
        elif action == "refund":
            serializer = RefundSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            refund, receipt = refund_payment(
                payment=payment,
                actor=request.gym_membership,
                amount=serializer.validated_data["amount"],
                reason=serializer.validated_data["reason"],
            )
            return success_response(
                {
                    "payment": PaymentSerializer(refund).data,
                    "receipt_number": receipt.receipt_number,
                },
                message="Payment refunded",
            )
        elif action == "void":
            serializer = ReasonSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            payment, reversal = void_payment(
                payment=payment,
                actor=request.gym_membership,
                reason=serializer.validated_data["reason"],
            )
            return success_response(
                {
                    "payment": PaymentSerializer(payment).data,
                    "reversal": PaymentSerializer(reversal).data,
                },
                message="Payment voided",
            )
        else:
            serializer = ReasonSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            reason = serializer.validated_data["reason"]
            now = timezone.now()
            if action == "reject" and payment.status == Payment.Status.PENDING_APPROVAL:
                payment.status = Payment.Status.REJECTED
                payment.rejected_at = now
                payment.rejected_by_membership_id = request.gym_membership.id
                payment.rejection_reason = reason
            elif action == "cancel" and payment.status == Payment.Status.DRAFT:
                payment.status = Payment.Status.CANCELLED
                payment.cancelled_at = now
                payment.cancellation_reason = reason
            else:
                from rest_framework.exceptions import ValidationError

                raise ValidationError({"status": ["Payment cannot perform this action."]})
            payment.updated_by_membership_id = request.gym_membership.id
            payment.save()
        return success_response(PaymentSerializer(payment).data, message="Payment updated")


class DashboardView(ReportsFeatureAPIView):
    required_permission = "reports.view"

    def get(self, request):
        today = gym_today(request.gym)
        range_name = request.query_params.get("range", "today")
        start = today.replace(day=1) if range_name == "month" else today
        payments = Payment.objects.filter(
            gym=request.gym,
            status__in=[Payment.Status.POSTED, Payment.Status.VOID],
            business_date__range=(start, today),
        ).select_related("membership")
        sources: dict[str, Decimal] = defaultdict(Decimal)
        for payment in payments:
            if payment.purpose == Payment.Purpose.MEMBERSHIP and payment.membership:
                source = payment.membership.revenue_source
            else:
                source = payment.purpose
            direction = (
                Decimal("-1")
                if payment.entry_type in [Payment.EntryType.REFUND, Payment.EntryType.REVERSAL]
                else Decimal("1")
            )
            sources[source] += direction * payment.amount
        ranked = [
            {"source": source, "amount": amount}
            for source, amount in sorted(sources.items(), key=lambda item: item[1], reverse=True)
        ]

        overdue_count = 0
        overdue_amount = Decimal("0")
        expired = Membership.objects.filter(
            gym=request.gym,
            expiry_date__lt=today,
            payment_status__in=[Membership.PaymentStatus.UNPAID, Membership.PaymentStatus.PARTIAL],
        ).prefetch_related("payments")
        for membership in expired:
            paid = sum(
                (
                    (
                        -payment.amount
                        if payment.entry_type
                        in [Payment.EntryType.REFUND, Payment.EntryType.REVERSAL]
                        else payment.amount
                    )
                    for payment in membership.payments.all()
                    if payment.status in [Payment.Status.POSTED, Payment.Status.VOID]
                ),
                Decimal("0"),
            )
            balance = max(Decimal("0"), membership.final_amount - paid)
            if balance:
                overdue_count += 1
                overdue_amount += balance
        expiring_count = Membership.objects.filter(
            gym=request.gym,
            status=Membership.Status.ACTIVE,
            expiry_date__range=(today, today + timedelta(days=30)),
        ).count()
        return success_response(
            {
                "range": range_name,
                "income": sum(sources.values(), Decimal("0")),
                "revenue_sources": ranked,
                "pending_payments": {"member_count": overdue_count, "amount": overdue_amount},
                "pending_approvals": Payment.objects.filter(
                    gym=request.gym, status=Payment.Status.PENDING_APPROVAL
                ).count(),
                "expiring_members": expiring_count,
                "attendance": {"available": False, "today": None},
                "currency": request.gym.currency,
            }
        )
