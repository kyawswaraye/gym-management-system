from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.common.api.responses import success_response
from apps.members.models import Member, Membership, MembershipPlan, Payment
from apps.members.serializers import (
    MemberCreateSerializer,
    MemberSerializer,
    MembershipCreateSerializer,
    MembershipPlanCreateSerializer,
    MembershipPlanSerializer,
    MembershipSerializer,
    PaymentCreateSerializer,
    PaymentSerializer,
    ReasonSerializer,
    RefundSerializer,
)
from apps.members.services import (
    approve_payment,
    cancel_membership,
    create_member,
    create_membership,
    create_plan,
    gym_today,
    refund_payment,
    resume_membership,
    submit_payment,
    suspend_membership,
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

    def get(self, request):
        queryset = Member.objects.filter(gym=request.gym).order_by("-created_at")
        if value := request.query_params.get("status"):
            queryset = queryset.filter(status=value)
        if search := request.query_params.get("search"):
            queryset = queryset.filter(
                Q(member_number__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(phone__icontains=search)
            )
        return success_response(MemberSerializer(queryset[:200], many=True).data)

    def post(self, request):
        serializer = MemberCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = create_member(
            gym=request.gym,
            actor=request.gym_membership,
            data=dict(serializer.validated_data),
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

    def get_object(self):
        return get_object_or_404(Member, gym=self.request.gym, id=self.kwargs["member_id"])

    def get(self, request, member_id):
        return success_response(MemberSerializer(self.get_object()).data)

    def patch(self, request, member_id):
        member = self.get_object()
        serializer = MemberSerializer(member, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by_membership_id=request.gym_membership.id)
        return success_response(serializer.data, message="Member updated")


class MemberArchiveView(MemberFeatureAPIView):
    required_permission = "members.archive"

    def post(self, request, member_id):
        member = get_object_or_404(Member, gym=request.gym, id=member_id)
        member.status = Member.Status.ARCHIVED
        member.archived_at = timezone.now()
        member.updated_by_membership_id = request.gym_membership.id
        member.save(
            update_fields=["status", "archived_at", "updated_by_membership_id", "updated_at"]
        )
        return success_response(MemberSerializer(member).data, message="Member archived")


class MemberSummaryView(MemberFeatureAPIView):
    required_permission = "members.view"

    def get(self, request, member_id):
        member = get_object_or_404(Member, gym=request.gym, id=member_id)
        memberships = member.memberships.select_related("plan").order_by("-start_date")
        payments = member.payments.order_by("-received_at")
        return success_response(
            {
                "member": MemberSerializer(member).data,
                "current_membership": MembershipSerializer(memberships.first()).data
                if memberships.exists()
                else None,
                "membership_history": MembershipSerializer(memberships[:20], many=True).data,
                "payment_history": PaymentSerializer(payments[:20], many=True).data,
            }
        )


class PlanListCreateView(MembershipFeatureAPIView):
    required_permission = "memberships.view"

    def get_permissions(self):
        self.required_permission = (
            "memberships.create" if self.request.method == "POST" else "memberships.view"
        )
        return super().get_permissions()

    def get(self, request):
        plans = MembershipPlan.objects.filter(gym=request.gym).order_by("name")
        return success_response(MembershipPlanSerializer(plans, many=True).data)

    def post(self, request):
        serializer = MembershipPlanCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan = create_plan(
            gym=request.gym, actor=request.gym_membership, data=dict(serializer.validated_data)
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

    def get_object(self):
        return get_object_or_404(MembershipPlan, gym=self.request.gym, id=self.kwargs["plan_id"])

    def get(self, request, plan_id):
        return success_response(MembershipPlanSerializer(self.get_object()).data)

    def patch(self, request, plan_id):
        plan = self.get_object()
        serializer = MembershipPlanSerializer(plan, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by_membership_id=request.gym_membership.id)
        return success_response(serializer.data, message="Membership plan updated")


class PlanArchiveView(MembershipFeatureAPIView):
    required_permission = "memberships.update"

    def post(self, request, plan_id):
        plan = get_object_or_404(MembershipPlan, gym=request.gym, id=plan_id)
        plan.is_active = False
        plan.archived_at = timezone.now()
        plan.updated_by_membership_id = request.gym_membership.id
        plan.save(
            update_fields=[
                "is_active",
                "archived_at",
                "updated_by_membership_id",
                "updated_at",
            ]
        )
        return success_response(MembershipPlanSerializer(plan).data, message="Plan archived")


class MembershipListCreateView(MembershipFeatureAPIView):
    required_permission = "memberships.view"

    def get_permissions(self):
        self.required_permission = (
            "memberships.create" if self.request.method == "POST" else "memberships.view"
        )
        return super().get_permissions()

    def get(self, request):
        memberships = Membership.objects.filter(gym=request.gym).select_related("member", "plan")
        if bucket := request.query_params.get("bucket"):
            today = gym_today(request.gym)
            ranges = {
                "expired": Q(
                    status__in=[
                        Membership.Status.ACTIVE,
                        Membership.Status.SUSPENDED,
                        Membership.Status.EXPIRED,
                    ],
                    expiry_date__lt=today,
                ),
                "today": Q(status=Membership.Status.ACTIVE, expiry_date=today),
                "within_3_days": Q(
                    status=Membership.Status.ACTIVE,
                    expiry_date__range=(today, today + timedelta(days=3)),
                ),
                "within_7_days": Q(
                    status=Membership.Status.ACTIVE,
                    expiry_date__range=(today, today + timedelta(days=7)),
                ),
                "within_30_days": Q(
                    status=Membership.Status.ACTIVE,
                    expiry_date__range=(today, today + timedelta(days=30)),
                ),
            }
            if bucket not in ranges:
                from rest_framework.exceptions import ValidationError

                raise ValidationError({"bucket": ["Unknown membership expiry bucket."]})
            memberships = memberships.filter(ranges[bucket])
        return success_response(
            MembershipSerializer(memberships.order_by("-start_date")[:200], many=True).data
        )

    def post(self, request):
        serializer = MembershipCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = get_object_or_404(
            Member, gym=request.gym, id=serializer.validated_data["member_id"]
        )
        plan = get_object_or_404(
            MembershipPlan,
            gym=request.gym,
            id=serializer.validated_data["plan_id"],
            is_active=True,
        )
        membership = create_membership(
            gym=request.gym,
            actor=request.gym_membership,
            member=member,
            plan=plan,
            start_date=serializer.validated_data["start_date"],
            discount_amount=serializer.validated_data["discount_amount"],
        )
        return success_response(
            MembershipSerializer(membership).data,
            message="Membership created",
            status=status.HTTP_201_CREATED,
        )


class MembershipActionView(MembershipFeatureAPIView):
    @property
    def required_permission(self):
        return (
            "memberships.cancel" if self.kwargs.get("action") == "cancel" else "memberships.update"
        )

    def post(self, request, membership_id, action):
        get_object_or_404(Membership, gym=request.gym, id=membership_id)
        if action == "cancel":
            serializer = ReasonSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            membership = cancel_membership(
                gym=request.gym,
                membership_id=membership_id,
                reason=serializer.validated_data["reason"],
            )
        elif action == "suspend":
            membership = suspend_membership(gym=request.gym, membership_id=membership_id)
        elif action == "resume":
            membership = resume_membership(gym=request.gym, membership_id=membership_id)
        else:
            from rest_framework.exceptions import ValidationError

            raise ValidationError({"action": ["Unknown membership action."]})
        return success_response(MembershipSerializer(membership).data, message="Membership updated")


class PaymentListCreateView(FinanceFeatureAPIView):
    required_permission = "payments.view"

    def get_permissions(self):
        self.required_permission = (
            "payments.create" if self.request.method == "POST" else "payments.view"
        )
        return super().get_permissions()

    def get(self, request):
        payments = Payment.objects.filter(gym=request.gym).select_related("member", "membership")
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
