from __future__ import annotations

import uuid
from typing import Any, cast

from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.api.exceptions import AuthenticationFailed
from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.tenants.models import Gym, GymMembership, Role
from apps.tenants.role_serializers import (
    CollectionQuerySerializer,
    MembershipRoleEnvelopeSerializer,
    MembershipRoleReplaceSerializer,
    PermissionListEnvelopeSerializer,
    RoleArchiveSerializer,
    RoleCreateSerializer,
    RoleEnvelopeSerializer,
    RoleListEnvelopeSerializer,
    RolePermissionReplaceSerializer,
    RoleUpdateSerializer,
)
from apps.tenants.role_services import (
    archive_role,
    create_role,
    get_membership_roles,
    page_permissions,
    page_roles,
    permission_payload,
    permission_queryset,
    replace_membership_roles,
    replace_role_permissions,
    role_payload,
    role_queryset,
    update_role,
)
from apps.tenants.staff_views import _request_context, _staff_actor
from apps.users.authentication import StaffJWTAuthentication
from apps.users.permissions import RequiresPermission


class RoleObjectPermission(RequiresPermission):
    """Re-check the view permission and reject another gym's role."""

    def has_object_permission(self, request: Request, view: APIView, obj: Role) -> bool:
        gym = getattr(request, "gym", None)
        return bool(
            isinstance(gym, Gym) and obj.gym_id == gym.id and self.has_permission(request, view)
        )


class MembershipRoleObjectPermission(RequiresPermission):
    """Re-check the view permission and reject owner or cross-gym memberships."""

    def has_object_permission(self, request: Request, view: APIView, obj: GymMembership) -> bool:
        gym = getattr(request, "gym", None)
        return bool(
            isinstance(gym, Gym)
            and obj.gym_id == gym.id
            and not obj.is_owner
            and self.has_permission(request, view)
        )


def _authz_version(request: Request) -> int:
    claims = request.auth
    version = claims.get("authz_version") if isinstance(claims, dict) else None
    if not isinstance(version, int) or version < 1:
        raise AuthenticationFailed()
    return version


def _collection_query(request: Request) -> dict[str, Any]:
    query = CollectionQuerySerializer(data=request.query_params)
    query.is_valid(raise_exception=True)
    return cast(dict[str, Any], query.validated_data)


class RoleListCreateView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresPermission]
    required_permission = "roles.view"

    def get_permissions(self) -> list[BasePermission]:
        self.required_permission = "roles.manage" if self.request.method == "POST" else "roles.view"
        return [permission() for permission in self.permission_classes]

    @extend_schema(
        operation_id="roles_list",
        parameters=[CollectionQuerySerializer],
        responses={
            200: RoleListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        gym, _membership = _staff_actor(request)
        filters = _collection_query(request)
        roles, meta = page_roles(
            role_queryset(gym),
            gym_id=gym.id,
            limit=int(filters["limit"]),
            after=str(filters.get("after", "")),
        )
        return success_response([role_payload(role) for role in roles], meta=meta)

    @extend_schema(
        operation_id="roles_create",
        request=RoleCreateSerializer,
        responses={
            201: RoleEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        serializer = RoleCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        gym, membership = _staff_actor(request)
        payload = serializer.validated_data
        role = create_role(
            gym_id=gym.id,
            actor=request.user,
            actor_membership_id=membership.id,
            expected_authz_version=_authz_version(request),
            key=str(payload["key"]),
            name=str(payload["name"]),
            description=str(payload.get("description", "")),
            is_active=bool(payload.get("is_active", True)),
            **_request_context(request),
        )
        return success_response(role, message="Role created", status=status.HTTP_201_CREATED)


class RoleDetailView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresPermission, RoleObjectPermission]
    required_permission = "roles.view"

    def get_permissions(self) -> list[BasePermission]:
        self.required_permission = (
            "roles.manage" if self.request.method == "PATCH" else "roles.view"
        )
        return [permission() for permission in self.permission_classes]

    @extend_schema(
        operation_id="roles_retrieve",
        responses={
            200: RoleEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, id: uuid.UUID) -> Response:
        gym, _membership = _staff_actor(request)
        role = _load_role(gym, id)
        self.check_object_permissions(request, role)
        return success_response(role_payload(role))

    @extend_schema(
        operation_id="roles_update",
        request=RoleUpdateSerializer,
        responses={
            200: RoleEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def patch(self, request: Request, id: uuid.UUID) -> Response:
        serializer = RoleUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        gym, membership = _staff_actor(request)
        role = _load_role(gym, id)
        self.check_object_permissions(request, role)
        updated = update_role(
            gym_id=gym.id,
            role_id=role.id,
            actor=request.user,
            actor_membership_id=membership.id,
            expected_authz_version=_authz_version(request),
            changes=dict(serializer.validated_data),
            **_request_context(request),
        )
        return success_response(updated, message="Role updated")


class RoleArchiveView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresPermission, RoleObjectPermission]
    required_permission = "roles.manage"

    @extend_schema(
        operation_id="roles_archive",
        request=RoleArchiveSerializer,
        responses={
            200: RoleEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: uuid.UUID) -> Response:
        RoleArchiveSerializer(data=request.data).is_valid(raise_exception=True)
        gym, membership = _staff_actor(request)
        role = _load_role(gym, id)
        self.check_object_permissions(request, role)
        archived = archive_role(
            gym_id=gym.id,
            role_id=role.id,
            actor=request.user,
            actor_membership_id=membership.id,
            expected_authz_version=_authz_version(request),
            **_request_context(request),
        )
        return success_response(archived, message="Role archived")


class PermissionListView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresPermission]
    required_permission = "roles.view"

    @extend_schema(
        operation_id="permissions_list",
        parameters=[CollectionQuerySerializer],
        responses={
            200: PermissionListEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        _staff_actor(request)
        filters = _collection_query(request)
        permissions, meta = page_permissions(
            permission_queryset(),
            limit=int(filters["limit"]),
            after=str(filters.get("after", "")),
        )
        return success_response(
            [permission_payload(permission) for permission in permissions], meta=meta
        )


class RolePermissionReplaceView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresPermission, RoleObjectPermission]
    required_permission = "roles.manage"

    @extend_schema(
        operation_id="roles_permissions_replace",
        request=RolePermissionReplaceSerializer,
        responses={
            200: RoleEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
        },
    )
    def put(self, request: Request, id: uuid.UUID) -> Response:
        serializer = RolePermissionReplaceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        gym, membership = _staff_actor(request)
        role = _load_role(gym, id)
        self.check_object_permissions(request, role)
        updated = replace_role_permissions(
            gym_id=gym.id,
            role_id=role.id,
            actor=request.user,
            actor_membership_id=membership.id,
            expected_authz_version=_authz_version(request),
            permission_codes=list(serializer.validated_data["permission_codes"]),
            **_request_context(request),
        )
        return success_response(updated, message="Role permissions replaced")


class MembershipRoleView(APIView):
    authentication_classes = [StaffJWTAuthentication]
    permission_classes = [IsAuthenticated, RequiresPermission, MembershipRoleObjectPermission]
    required_permission = "staff.view"

    def get_permissions(self) -> list[BasePermission]:
        self.required_permission = "staff.manage" if self.request.method == "PUT" else "staff.view"
        return [permission() for permission in self.permission_classes]

    @extend_schema(
        operation_id="membership_roles_retrieve",
        responses={
            200: MembershipRoleEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, id: uuid.UUID) -> Response:
        gym, _membership = _staff_actor(request)
        membership = _load_membership(gym, id)
        self.check_object_permissions(request, membership)
        return success_response(get_membership_roles(gym_id=gym.id, membership_id=membership.id))

    @extend_schema(
        operation_id="membership_roles_replace",
        request=MembershipRoleReplaceSerializer,
        responses={
            200: MembershipRoleEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
        },
    )
    def put(self, request: Request, id: uuid.UUID) -> Response:
        serializer = MembershipRoleReplaceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        gym, membership = _staff_actor(request)
        target = _load_membership(gym, id)
        self.check_object_permissions(request, target)
        updated = replace_membership_roles(
            gym_id=gym.id,
            membership_id=target.id,
            actor=request.user,
            actor_membership_id=membership.id,
            expected_authz_version=_authz_version(request),
            role_ids=list(serializer.validated_data["role_ids"]),
            **_request_context(request),
        )
        return success_response(updated, message="Membership roles replaced")


def _load_role(gym: Gym, role_id: uuid.UUID) -> Role:
    try:
        return role_queryset(gym).get(id=role_id)
    except Role.DoesNotExist as exc:
        raise Http404 from exc


def _load_membership(gym: Gym, membership_id: uuid.UUID) -> GymMembership:
    try:
        return GymMembership.objects.get(id=membership_id, gym=gym, is_owner=False)
    except GymMembership.DoesNotExist as exc:
        raise Http404 from exc
