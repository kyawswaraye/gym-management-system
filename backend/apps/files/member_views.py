"""Member profile-photo routes. Staff file authentication is not used."""

from __future__ import annotations

from typing import Any, NoReturn
from uuid import UUID

from django.db import transaction
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.files.exceptions import FileRequestError, RateLimited
from apps.files.runtime import image_processor, object_store, virus_scanner
from apps.files.serializers import (
    CompletedFileEnvelopeSerializer,
    CompleteUploadSerializer,
    DeletedFileEnvelopeSerializer,
    DownloadUrlEnvelopeSerializer,
    MemberProfilePhotoIntentSerializer,
    UploadIntentEnvelopeSerializer,
    validated_file_data,
)
from apps.files.services import (
    RequestAudit,
    complete_upload,
    create_member_profile_photo_intent,
    delete_upload,
    issue_download_url,
)
from apps.files.views import _command_response, _reject_query
from apps.users.authentication import MemberJWTAuthentication
from apps.users.throttling import MemberFileDownloadThrottle, MemberFileWriteThrottle

_MEMBER_INTENT_FIELDS = ("original_name", "content_type", "byte_size", "sha256")
_COMPLETE_FIELDS = ("sha256", "byte_size")


def _member_audit(request: Request) -> RequestAudit:
    address = request.META.get("REMOTE_ADDR") or None
    return RequestAudit(
        actor_user=request.user,
        actor_membership_id=None,
        request_id=str(getattr(request, "request_id", "") or "req_unknown"),
        ip_address=address if isinstance(address, str) else None,
        user_agent=str(request.META.get("HTTP_USER_AGENT", ""))[:512],
    )


def _reject_idempotency(request: Request) -> None:
    if request.headers.get("Idempotency-Key"):
        raise ValidationError(
            {"Idempotency-Key": ["Idempotency-Key is not accepted for a member file route."]}
        )


class MemberFileView(APIView):
    authentication_classes = [MemberJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def throttled(self, request: Request, wait: float | None) -> NoReturn:
        raise RateLimited()

    @method_decorator(transaction.non_atomic_requests)
    def dispatch(self, request: Any, *args: Any, **kwargs: Any) -> HttpResponse:
        self.args = args
        self.kwargs = kwargs
        request = self.initialize_request(request, *args, **kwargs)
        self.request = request
        self.headers = self.default_response_headers
        try:
            with transaction.atomic():
                self.initial(request, *args, **kwargs)
            if request.method.lower() in self.http_method_names:
                handler = getattr(self, request.method.lower(), self.http_method_not_allowed)
            else:
                handler = self.http_method_not_allowed
            response = handler(request, *args, **kwargs)
        except Exception as exc:
            response = self.handle_exception(exc)
        self.response = self.finalize_response(request, response, *args, **kwargs)
        return self.response


class MemberUploadIntentView(MemberFileView):
    throttle_classes = [MemberFileWriteThrottle]

    @extend_schema(
        operation_id="member_files_upload_intent",
        request=MemberProfilePhotoIntentSerializer,
        responses={
            201: UploadIntentEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
            503: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        _reject_query(request)
        _reject_idempotency(request)
        payload = validated_file_data(
            MemberProfilePhotoIntentSerializer(data=request.data),
            _MEMBER_INTENT_FIELDS,
        )
        result = create_member_profile_photo_intent(
            gym=request.gym,
            member=request.member,
            audit=_member_audit(request),
            store=object_store(),
            original_name=str(payload["original_name"]),
            content_type=str(payload["content_type"]),
            byte_size=int(payload["byte_size"]),
            sha256=str(payload["sha256"]),
        )
        return _command_response(result)


class MemberCompleteUploadView(MemberFileView):
    throttle_classes = [MemberFileWriteThrottle]

    @extend_schema(
        operation_id="member_files_complete_upload",
        request=CompleteUploadSerializer,
        responses={
            200: CompletedFileEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
            503: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request, id: UUID) -> Response:
        _reject_query(request)
        _reject_idempotency(request)
        payload = validated_file_data(CompleteUploadSerializer(data=request.data), _COMPLETE_FIELDS)
        result = complete_upload(
            gym=request.gym,
            audit=_member_audit(request),
            store=object_store(),
            scanner=virus_scanner(),
            canonicalize=image_processor(),
            asset_id=id,
            sha256=str(payload["sha256"]),
            byte_size=int(payload["byte_size"]),
            member_self=request.member,
        )
        return _command_response(result)


class MemberDownloadUrlView(MemberFileView):
    throttle_classes = [MemberFileDownloadThrottle]

    @extend_schema(
        operation_id="member_files_download_url",
        responses={
            200: DownloadUrlEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            409: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
            503: ErrorEnvelopeSerializer,
        },
    )
    def get(self, request: Request, id: UUID) -> Response:
        _reject_query(request)
        _reject_idempotency(request)
        result = issue_download_url(
            gym=request.gym,
            audit=_member_audit(request),
            store=object_store(),
            asset_id=id,
            member_self=request.member,
        )
        return _command_response(result)


class MemberDeleteUploadView(MemberFileView):
    throttle_classes = [MemberFileWriteThrottle]

    @extend_schema(
        operation_id="member_files_delete_upload",
        request=None,
        responses={
            200: DeletedFileEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
            503: ErrorEnvelopeSerializer,
        },
    )
    def delete(self, request: Request, id: UUID) -> Response:
        _reject_query(request)
        _reject_idempotency(request)
        if request.body.strip():
            raise FileRequestError(
                code="INVALID_REQUEST",
                message="The request could not be processed.",
                status_code=400,
            )
        result = delete_upload(
            gym=request.gym,
            audit=_member_audit(request),
            store=object_store(),
            asset_id=id,
            member_self=request.member,
        )
        return _command_response(result)
