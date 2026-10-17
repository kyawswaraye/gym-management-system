from __future__ import annotations

from typing import Any, NoReturn
from uuid import UUID

from django.db import transaction
from django.utils.decorators import method_decorator
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.api.responses import success_response
from apps.common.api.schema import ErrorEnvelopeSerializer
from apps.files.authentication import StaffFileAuthentication
from apps.files.exceptions import FileRequestError, RateLimited
from apps.files.runtime import image_processor, object_store, virus_scanner
from apps.files.serializers import (
    _COMPLETE_FIELDS,
    _INTENT_FIELDS,
    CompletedFileEnvelopeSerializer,
    CompleteUploadSerializer,
    DeletedFileEnvelopeSerializer,
    DownloadUrlEnvelopeSerializer,
    UploadIntentEnvelopeSerializer,
    UploadIntentSerializer,
    validated_file_data,
)
from apps.files.services import (
    FileCommand,
    RequestAudit,
    complete_upload,
    create_upload_intent,
    delete_upload,
    issue_download_url,
)
from apps.files.throttling import (
    FileCompleteThrottle,
    FileDeleteThrottle,
    FileDownloadThrottle,
    UploadIntentThrottle,
)
from apps.users.permissions import RequiresPermissions


def _json_ready(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def _reject_query(request: Request) -> None:
    names = list(request.query_params)
    if not names:
        return
    raise FileRequestError(
        code="INVALID_REQUEST",
        message="The request could not be processed.",
        status_code=400,
        fields={name: ["This field is not allowed."] for name in sorted(set(names))},
    )


def _request_audit(request: Request) -> RequestAudit:
    address = request.META.get("REMOTE_ADDR") or None
    return RequestAudit(
        actor_user=request.user,
        actor_membership_id=request.gym_membership.id,
        request_id=getattr(request, "request_id", "req_unknown"),
        ip_address=address if isinstance(address, str) else None,
        user_agent=str(request.META.get("HTTP_USER_AGENT", ""))[:512],
    )


def _command_response(result: FileCommand) -> Response:
    return success_response(
        _json_ready(result.data),
        message=result.message,
        status=result.status_code,
    )


class FileAPIView(APIView):
    authentication_classes = [StaffFileAuthentication]
    permission_classes = [IsAuthenticated, RequiresPermissions]

    def throttled(self, request: Request, wait: float | None) -> NoReturn:
        raise RateLimited()


class CommittedFileView(FileAPIView):
    """Authenticate in a short transaction, then commit each service transaction alone."""

    @method_decorator(transaction.non_atomic_requests)
    def dispatch(self, request: Any, *args: Any, **kwargs: Any) -> Response:
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


class UploadIntentView(CommittedFileView):
    required_permissions = ("files.upload", "members.update")
    throttle_classes = [UploadIntentThrottle]

    @extend_schema(
        operation_id="files_upload_intent",
        request=UploadIntentSerializer,
        responses={
            201: UploadIntentEnvelopeSerializer,
            400: ErrorEnvelopeSerializer,
            401: ErrorEnvelopeSerializer,
            403: ErrorEnvelopeSerializer,
            404: ErrorEnvelopeSerializer,
            422: ErrorEnvelopeSerializer,
            429: ErrorEnvelopeSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        _reject_query(request)
        payload = validated_file_data(UploadIntentSerializer(data=request.data), _INTENT_FIELDS)
        result = create_upload_intent(
            gym=request.gym,
            actor=request.gym_membership,
            audit=_request_audit(request),
            store=object_store(),
            owner_id=payload["owner_id"],
            original_name=str(payload["original_name"]),
            content_type=str(payload["content_type"]),
            byte_size=int(payload["byte_size"]),
            sha256=str(payload["sha256"]),
        )
        return _command_response(result)


class CompleteUploadView(CommittedFileView):
    required_permissions = ("files.upload", "members.update")
    throttle_classes = [FileCompleteThrottle]

    @extend_schema(
        operation_id="files_complete_upload",
        request=CompleteUploadSerializer,
        parameters=[
            OpenApiParameter(
                name="Retry-After",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.HEADER,
                response=[409],
                required=False,
                description=(
                    "Whole seconds to wait before retrying a 409 FILE_SCAN_IN_PROGRESS "
                    "response, from 1 through 120. Other 409 responses omit this header."
                ),
            ),
        ],
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
        payload = validated_file_data(CompleteUploadSerializer(data=request.data), _COMPLETE_FIELDS)
        result = complete_upload(
            gym=request.gym,
            actor=request.gym_membership,
            audit=_request_audit(request),
            store=object_store(),
            scanner=virus_scanner(),
            canonicalize=image_processor(),
            asset_id=id,
            sha256=str(payload["sha256"]),
            byte_size=int(payload["byte_size"]),
        )
        return _command_response(result)


class DownloadUrlView(CommittedFileView):
    required_permissions = ("files.view", "members.view")
    throttle_classes = [FileDownloadThrottle]

    @extend_schema(
        operation_id="files_download_url",
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
        result = issue_download_url(
            gym=request.gym,
            actor=request.gym_membership,
            audit=_request_audit(request),
            store=object_store(),
            asset_id=id,
        )
        return _command_response(result)


class DeleteUploadView(CommittedFileView):
    required_permissions = ("files.delete", "members.update")
    throttle_classes = [FileDeleteThrottle]

    @extend_schema(
        operation_id="files_delete_upload",
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
        if request.body.strip():
            raise FileRequestError(
                code="INVALID_REQUEST",
                message="The request could not be processed.",
                status_code=400,
            )
        result = delete_upload(
            gym=request.gym,
            actor=request.gym_membership,
            audit=_request_audit(request),
            store=object_store(),
            asset_id=id,
        )
        return _command_response(result)
