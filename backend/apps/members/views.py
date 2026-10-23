from __future__ import annotations

from uuid import UUID

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.members.challenge_services import issue_staff_check_in_challenge
from apps.members.checkin_services import (
    check_out_check_in,
    create_manual_check_in,
    get_check_in,
    page_check_ins,
    page_member_check_ins,
    parse_idempotency_key,
)
from apps.members.models import Member, Membership, MembershipPlan
from apps.members.serializers import (
    CheckInChallengeCreateSerializer,
    CheckInChallengeEnvelopeSerializer,
    CheckInChallengeIssuedSerializer,
    CheckInCheckoutSerializer,
    CheckInCreateSerializer,
    CheckInEnvelopeSerializer,
    CheckInListEnvelopeSerializer,
    CheckInListQuerySerializer,
    CheckInSerializer,
    MemberArchiveSerializer,
    MemberCreateSerializer,
    MemberEnvelopeSerializer,
    MemberListEnvelopeSerializer,
    MemberListQuerySerializer,
    MemberPaymentHistoryEnvelopeSerializer,
    MemberPaymentHistoryItemSerializer,
    MemberPaymentHistoryQuerySerializer,
    MemberRestoreSerializer,
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
)
from apps.members.services import (
    archive_member,
    archive_plan,
    cancel_membership,
    create_member,
    create_membership,
    create_plan,
    gym_today,
    member_summary,
    page_member_payments,
    page_members,
    page_membership_expiry,
    page_membership_plans,
    page_memberships,
    restore_member,
    resume_membership,
    suspend_membership,
    update_member,
    update_membership_note,
    update_plan,
)
from apps.trainers.selectors import (
    deny_trainer_only,
    require_assigned_member,
    require_visible_membership,
    scope_to_assigned_members,
)
from apps.users.permissions import (
    RequiresEntitlement,
    RequiresEntitlements,
    RequiresPermission,
    RequiresPermissions,
)


class TenantAPIView(APIView):
    permission_classes = [IsAuthenticated, RequiresPermission, RequiresEntitlement]


class MemberFeatureAPIView(TenantAPIView):
    required_feature = "members"


class MembershipFeatureAPIView(TenantAPIView):
    required_feature = "memberships"


class FinanceFeatureAPIView(TenantAPIView):
    required_feature = "finance"


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
            scope_to_assigned_members(
                Member.objects.filter(gym=request.gym),
                gym_id=request.gym.id,
                actor=request.gym_membership,
                as_of=gym_today(request.gym),
                member_ref="pk",
            ),
            gym_id=request.gym.id,
            status=str(filters.get("status", "")),
            search=str(filters.get("search", "")),
            joined_from=filters.get("joined_from"),
            joined_to=filters.get("joined_to"),
            is_pt_client=filters.get("is_pt_client"),
            as_of=gym_today(request.gym),
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
        from apps.trainers.selectors import annotate_member_pt_client

        as_of = gym_today(self.request.gym)
        queryset = scope_to_assigned_members(
            annotate_member_pt_client(
                Member.objects.filter(gym=self.request.gym),
                gym_id=self.request.gym.id,
                as_of=as_of,
            ),
            gym_id=self.request.gym.id,
            actor=self.request.gym_membership,
            as_of=as_of,
            member_ref="pk",
        )
        return get_object_or_404(queryset, id=self.kwargs["id"])

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


class MemberRestoreView(MemberFeatureAPIView):
    required_permission = "members.restore"

    @extend_schema(
        operation_id="members_restore",
        request=MemberRestoreSerializer,
        responses={
            200: MemberEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> Response:
        MemberRestoreSerializer(data=request.data).is_valid(raise_exception=True)
        address = request.META.get("REMOTE_ADDR")
        member = restore_member(
            gym=request.gym,
            actor=request.gym_membership,
            member_id=id,
            request_id=str(getattr(request, "request_id", "") or "req_unknown"),
            ip_address=str(address) if address else None,
            user_agent=str(request.META.get("HTTP_USER_AGENT", "")),
        )
        return success_response(MemberSerializer(member).data, message="Member restored")


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
        from apps.trainers.selectors import annotate_member_pt_client

        as_of = gym_today(request.gym)
        member = get_object_or_404(
            scope_to_assigned_members(
                annotate_member_pt_client(
                    Member.objects.filter(gym=request.gym),
                    gym_id=request.gym.id,
                    as_of=as_of,
                ),
                gym_id=request.gym.id,
                actor=request.gym_membership,
                as_of=as_of,
                member_ref="pk",
            ),
            id=id,
        )
        payload = member_summary(gym=request.gym, member=member)
        return success_response(MemberSummarySerializer(payload).data)


class MemberPaymentHistoryView(APIView):
    permission_classes = [IsAuthenticated, RequiresPermissions, RequiresEntitlements]
    required_permissions = ("members.view", "payments.view")
    required_features = ("members", "finance")

    @extend_schema(
        operation_id="members_payment_history",
        parameters=[MemberPaymentHistoryQuerySerializer],
        responses={
            200: MemberPaymentHistoryEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, id: UUID) -> Response:
        deny_trainer_only(gym_id=request.gym.id, actor=request.gym_membership)
        query = MemberPaymentHistoryQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        filters = query.validated_data
        member = get_object_or_404(Member, gym=request.gym, id=id)
        payments, meta = page_member_payments(
            gym_id=request.gym.id,
            member_id=member.id,
            limit=int(filters["limit"]),
            after=str(filters.get("after", "")),
        )
        return success_response(
            MemberPaymentHistoryItemSerializer(payments, many=True).data,
            meta=meta,
        )


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
        as_of = gym_today(request.gym)
        if member_id is not None:
            require_assigned_member(
                gym_id=request.gym.id,
                actor=request.gym_membership,
                member_id=member_id,
                as_of=as_of,
            )
            get_object_or_404(Member, gym=request.gym, id=member_id)
        memberships, meta = page_memberships(
            scope_to_assigned_members(
                Membership.objects.filter(gym=request.gym),
                gym_id=request.gym.id,
                actor=request.gym_membership,
                as_of=as_of,
                member_ref="member_id",
            ),
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
        require_assigned_member(
            gym_id=request.gym.id,
            actor=request.gym_membership,
            member_id=payload["member_id"],
            as_of=gym_today(request.gym),
        )
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
        require_visible_membership(
            gym=self.request.gym,
            actor=self.request.gym_membership,
            membership_id=self.kwargs["id"],
        )
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
        require_visible_membership(
            gym=request.gym,
            actor=request.gym_membership,
            membership_id=id,
        )
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
        require_visible_membership(
            gym=request.gym,
            actor=request.gym_membership,
            membership_id=id,
        )
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
        require_visible_membership(
            gym=request.gym,
            actor=request.gym_membership,
            membership_id=id,
        )
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
        require_visible_membership(
            gym=request.gym,
            actor=request.gym_membership,
            membership_id=id,
        )
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
            scope_to_assigned_members(
                Membership.objects.filter(gym=request.gym),
                gym_id=request.gym.id,
                actor=request.gym_membership,
                as_of=gym_today(request.gym),
                member_ref="member_id",
            ),
            gym_id=request.gym.id,
            bucket=str(filters["bucket"]),
            today=gym_today(request.gym),
            limit=int(filters["limit"]),
            after=str(filters.get("after", "")),
        )
        return success_response(MembershipSerializer(memberships, many=True).data, meta=meta)


_CHECK_IN_IDEMPOTENCY = OpenApiParameter(
    name="Idempotency-Key",
    type=str,
    location=OpenApiParameter.HEADER,
    required=False,
    description="Optional replay key for manual check-in. Checkout does not use it.",
)


def _request_id(request: Request) -> str:
    return str(getattr(request, "request_id", "") or "req_unknown")


def _client_address(request: Request) -> str | None:
    address = request.META.get("REMOTE_ADDR")
    return str(address) if address else None


def _user_agent(request: Request) -> str:
    return str(request.META.get("HTTP_USER_AGENT", ""))


class CheckInAPIView(APIView):
    permission_classes = [IsAuthenticated, RequiresPermission, RequiresEntitlements]
    required_permission = "checkins.view"
    required_features = ("members", "memberships")


class CheckInListCreateView(CheckInAPIView):
    def get_permissions(self):
        self.required_permission = (
            "checkins.create" if self.request.method == "POST" else "checkins.view"
        )
        return super().get_permissions()

    @extend_schema(
        operation_id="check_ins_list",
        parameters=[CheckInListQuerySerializer],
        responses={
            200: CheckInListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        query = CheckInListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        filters = query.validated_data
        rows, meta = page_check_ins(
            gym=request.gym,
            actor=request.gym_membership,
            member_id=filters.get("member_id"),
            open_state=str(filters.get("open", "")),
            limit=int(filters["limit"]),
            after=str(filters.get("after", "")),
        )
        return success_response(CheckInSerializer(rows, many=True).data, meta=meta)

    @extend_schema(
        operation_id="check_ins_create",
        parameters=[_CHECK_IN_IDEMPOTENCY],
        request=CheckInCreateSerializer,
        responses={
            200: CheckInEnvelopeSerializer,
            201: CheckInEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        serializer = CheckInCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        if "occurred_at" in payload and "checkins.update" not in request.permission_codes:
            raise PermissionDenied("Backdated check-in requires check-in correction permission.")
        result = create_manual_check_in(
            gym=request.gym,
            actor=request.gym_membership,
            member_id=payload["member_id"],
            occurred_at=payload.get("occurred_at"),
            note=str(payload.get("note", "")),
            idempotency_key=parse_idempotency_key(request.headers.get("Idempotency-Key")),
            request_id=_request_id(request),
            ip_address=_client_address(request),
            user_agent=_user_agent(request),
        )
        return success_response(
            CheckInSerializer(result.check_in).data,
            message="Check-in recorded" if result.created else "Check-in already recorded",
            status=status.HTTP_201_CREATED if result.created else status.HTTP_200_OK,
        )


class CheckInDetailView(CheckInAPIView):
    @extend_schema(
        operation_id="check_ins_retrieve",
        responses={
            200: CheckInEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, id: UUID) -> Response:
        check_in = get_check_in(gym=request.gym, actor=request.gym_membership, check_in_id=id)
        return success_response(CheckInSerializer(check_in).data)


class CheckInCheckoutView(CheckInAPIView):
    required_permission = "checkins.update"

    @extend_schema(
        operation_id="check_ins_check_out",
        request=CheckInCheckoutSerializer,
        responses={
            200: CheckInEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> Response:
        serializer = CheckInCheckoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        check_in = check_out_check_in(
            gym=request.gym,
            actor=request.gym_membership,
            check_in_id=id,
            occurred_at=serializer.validated_data.get("occurred_at"),
            request_id=_request_id(request),
            ip_address=_client_address(request),
            user_agent=_user_agent(request),
        )
        return success_response(CheckInSerializer(check_in).data, message="Checked out")


class MemberCheckInHistoryView(APIView):
    permission_classes = [IsAuthenticated, RequiresPermissions, RequiresEntitlements]
    required_permissions = ("members.view", "checkins.view")
    required_features = ("members", "memberships")

    @extend_schema(
        operation_id="members_check_in_history",
        parameters=[MemberPaymentHistoryQuerySerializer],
        responses={
            200: CheckInListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, id: UUID) -> Response:
        query = MemberPaymentHistoryQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        filters = query.validated_data
        rows, meta = page_member_check_ins(
            gym=request.gym,
            actor=request.gym_membership,
            member_id=id,
            limit=int(filters["limit"]),
            after=str(filters.get("after", "")),
        )
        return success_response(CheckInSerializer(rows, many=True).data, meta=meta)


class CheckInChallengeIssueView(APIView):
    permission_classes = [IsAuthenticated, RequiresPermission, RequiresEntitlements]
    required_permission = "checkins.challenge.issue"
    required_features = ("members", "memberships")

    @extend_schema(
        operation_id="check_in_challenges_create",
        request=CheckInChallengeCreateSerializer,
        responses={
            201: CheckInChallengeEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        if request.headers.get("Idempotency-Key"):
            raise ValidationError(
                {"Idempotency-Key": ["Idempotency-Key is not accepted for a check-in challenge."]}
            )
        serializer = CheckInChallengeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        issued = issue_staff_check_in_challenge(
            gym=request.gym,
            actor=request.gym_membership,
            display_location=str(serializer.validated_data["display_location"]),
            request_id=_request_id(request),
            ip_address=_client_address(request),
            user_agent=_user_agent(request),
        )
        challenge = issued.challenge
        return success_response(
            CheckInChallengeIssuedSerializer(
                {
                    "id": challenge.id,
                    "payload": issued.payload,
                    "display_location": challenge.display_location,
                    "issued_at": challenge.issued_at,
                    "expires_at": challenge.expires_at,
                    "max_uses": challenge.max_uses,
                    "status": challenge.status,
                }
            ).data,
            message="Challenge issued",
            status=status.HTTP_201_CREATED,
        )
