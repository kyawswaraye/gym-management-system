from __future__ import annotations

import re
from typing import Any, cast

from rest_framework import serializers
from rest_framework.exceptions import ErrorDetail

from apps.files.constants import (
    ALLOWED_CONTENT_TYPES,
    EXPENSE_RECEIPT_PURPOSE,
    GYM_OWNER,
    MAX_UPLOAD_BYTES,
    MEMBER_OWNER,
    PROFILE_PHOTO_PURPOSE,
)
from apps.files.exceptions import FileRequestError
from apps.users.serializers import StrictSerializer

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_INTENT_FIELDS = (
    "original_name",
    "content_type",
    "byte_size",
    "sha256",
    "purpose",
    "owner_type",
    "owner_id",
)
_COMPLETE_FIELDS = ("sha256", "byte_size")


class OriginalNameField(serializers.CharField):
    def to_internal_value(self, data: Any) -> str:
        if not isinstance(data, str):
            raise serializers.ValidationError("Enter a valid file name.", code="INVALID_REQUEST")
        value = data.strip()
        if (
            not value
            or len(value) > 255
            or "/" in value
            or "\\" in value
            or any(ord(character) < 32 for character in value)
        ):
            raise serializers.ValidationError("Enter a valid file name.", code="INVALID_REQUEST")
        return value


class DeclaredSha256Field(serializers.CharField):
    def to_internal_value(self, data: Any) -> str:
        if not isinstance(data, str) or _SHA256.fullmatch(data) is None:
            raise serializers.ValidationError(
                "The checksum is invalid.", code="FILE_CHECKSUM_INVALID"
            )
        return data


class DeclaredSizeField(serializers.IntegerField):
    def to_internal_value(self, data: Any) -> int:
        if isinstance(data, bool) or not isinstance(data, int):
            raise serializers.ValidationError(
                "Enter a whole number of bytes.", code="INVALID_REQUEST"
            )
        if data < 1:
            raise serializers.ValidationError("The file is empty.", code="FILE_EMPTY")
        if data > MAX_UPLOAD_BYTES:
            raise serializers.ValidationError("The file is too large.", code="FILE_TOO_LARGE")
        return int(data)


class UploadIntentSerializer(StrictSerializer):
    original_name = OriginalNameField()
    content_type = serializers.CharField()
    byte_size = DeclaredSizeField()
    sha256 = DeclaredSha256Field()
    purpose = serializers.CharField()
    owner_type = serializers.CharField()
    owner_id = serializers.UUIDField()

    def validate_content_type(self, value: str) -> str:
        if value not in ALLOWED_CONTENT_TYPES:
            raise serializers.ValidationError(
                "This file type is not allowed.", code="FILE_TYPE_NOT_ALLOWED"
            )
        return value

    def validate_purpose(self, value: str) -> str:
        if value not in {PROFILE_PHOTO_PURPOSE, EXPENSE_RECEIPT_PURPOSE}:
            raise serializers.ValidationError(
                "This file purpose is not supported.", code="UNSUPPORTED_FILE_PURPOSE"
            )
        return value

    def validate_owner_type(self, value: str) -> str:
        if value not in {MEMBER_OWNER, GYM_OWNER}:
            raise serializers.ValidationError(
                "This file owner is not supported.", code="UNSUPPORTED_FILE_OWNER"
            )
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        purpose = attrs.get("purpose")
        owner_type = attrs.get("owner_type")
        if purpose == PROFILE_PHOTO_PURPOSE and owner_type != MEMBER_OWNER:
            raise serializers.ValidationError(
                {
                    "owner_type": [
                        ErrorDetail(
                            "This file owner is not supported.",
                            code="UNSUPPORTED_FILE_OWNER",
                        )
                    ]
                }
            )
        if purpose == EXPENSE_RECEIPT_PURPOSE and owner_type != GYM_OWNER:
            raise serializers.ValidationError(
                {
                    "owner_type": [
                        ErrorDetail(
                            "This file owner is not supported.",
                            code="UNSUPPORTED_FILE_OWNER",
                        )
                    ]
                }
            )
        return attrs


class CompleteUploadSerializer(StrictSerializer):
    sha256 = DeclaredSha256Field()
    byte_size = DeclaredSizeField()


class UploadTargetSerializer(serializers.Serializer):
    method = serializers.CharField()
    url = serializers.CharField()
    file_field = serializers.CharField()
    expires_at = serializers.CharField()
    fields = serializers.DictField(child=serializers.CharField())


class UploadIntentDataSerializer(serializers.Serializer):
    file_id = serializers.UUIDField()
    purpose = serializers.CharField()
    owner_type = serializers.CharField()
    owner_id = serializers.UUIDField()
    upload = UploadTargetSerializer()


class UploadIntentEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = UploadIntentDataSerializer()
    message = serializers.CharField()


class CompletedFileSerializer(serializers.Serializer):
    file_id = serializers.UUIDField()
    purpose = serializers.CharField()
    owner_type = serializers.CharField()
    owner_id = serializers.UUIDField()
    original_name = serializers.CharField()
    content_type = serializers.CharField()
    byte_size = serializers.IntegerField()
    sha256 = serializers.CharField()
    scan_status = serializers.CharField()
    retention_status = serializers.CharField()
    attachable = serializers.BooleanField()


class CompletedFileEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = CompletedFileSerializer()
    message = serializers.CharField()


class DownloadUrlSerializer(serializers.Serializer):
    url = serializers.CharField()
    method = serializers.CharField()
    expires_at = serializers.CharField()
    filename = serializers.CharField()
    content_type = serializers.CharField()
    byte_size = serializers.IntegerField()
    sha256 = serializers.CharField()


class DownloadUrlEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = DownloadUrlSerializer()
    message = serializers.CharField()


class DeletedFileSerializer(serializers.Serializer):
    file_id = serializers.UUIDField()
    purpose = serializers.CharField()
    owner_type = serializers.CharField()
    owner_id = serializers.UUIDField()
    scan_status = serializers.CharField()
    retention_status = serializers.CharField()
    rejection_code = serializers.CharField(allow_null=True)
    deleted_at = serializers.CharField(allow_null=True)


class DeletedFileEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = DeletedFileSerializer()
    message = serializers.CharField()


def validated_file_data(serializer: StrictSerializer, fields: tuple[str, ...]) -> dict[str, Any]:
    if serializer.is_valid():
        return cast(dict[str, Any], serializer.validated_data)
    errors = serializer.errors
    unknown = sorted(set(errors) - set(fields))
    if unknown:
        field = unknown[0]
        raise FileRequestError(
            code="INVALID_REQUEST",
            message="The request could not be processed.",
            status_code=400,
            fields={field: ["This field is not allowed."]},
        )
    for field in fields:
        if field not in errors:
            continue
        detail = errors[field][0]
        code = str(getattr(detail, "code", "invalid")).upper()
        message = str(detail)
        status_code, stable = _field_status(field, code)
        raise FileRequestError(
            code=stable,
            message=message,
            status_code=status_code,
            fields={field: [message]},
        )
    raise FileRequestError(
        code="INVALID_REQUEST",
        message="The request could not be processed.",
        status_code=400,
    )


def _field_status(field: str, code: str) -> tuple[int, str]:
    if field == "original_name":
        return 422, "INVALID_REQUEST"
    if field == "byte_size" and code == "INVALID_REQUEST":
        return 400, "INVALID_REQUEST"
    if field in {"owner_type", "owner_id"} and code in {"INVALID", "REQUIRED", "NULL"}:
        return 422, "UNSUPPORTED_FILE_OWNER"
    if code in {"INVALID", "REQUIRED", "NULL", "BLANK"}:
        return 400, "INVALID_REQUEST"
    return 422, code
