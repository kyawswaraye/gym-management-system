from __future__ import annotations

from django.conf import settings
from django.db import connection
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .responses import success_response
from .schema import ErrorEnvelopeSerializer, HealthEnvelopeSerializer, ReadinessEnvelopeSerializer


class HealthView(APIView):
    authentication_classes: list[type] = []
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="health",
        responses={200: HealthEnvelopeSerializer},
        auth=[],
    )
    def get(self, request: Request) -> Response:
        return success_response({"status": "ok"})


class ReadinessView(APIView):
    authentication_classes: list[type] = []
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="readiness",
        responses={200: ReadinessEnvelopeSerializer, 503: ErrorEnvelopeSerializer},
        auth=[],
    )
    def get(self, request: Request) -> Response:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        key_ready = bool(settings.JWT_KEY_ID and settings.JWT_PUBLIC_KEYS)
        if not key_ready:
            return Response(
                {
                    "success": False,
                    "error": {
                        "code": "SERVICE_NOT_READY",
                        "message": "The service is not ready.",
                        "fields": {},
                    },
                    "request_id": getattr(request, "request_id", "req_unknown"),
                },
                status=503,
            )
        return success_response(
            {"status": "ready", "database": "ok", "jwt_keys": True},
        )
