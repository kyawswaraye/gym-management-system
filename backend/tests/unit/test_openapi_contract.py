from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.renderers import OpenApiJsonRenderer

PHASE_1A_PATHS = {
    "/api/v1/auth/gyms/",
    "/api/v1/auth/login/",
    "/api/v1/auth/logout/",
    "/api/v1/auth/logout-all/",
    "/api/v1/auth/me/",
    "/api/v1/auth/mfa/challenge/",
    "/api/v1/auth/password/forgot/",
    "/api/v1/auth/password/reset/",
    "/api/v1/auth/refresh/",
    "/api/v1/auth/select-gym/",
    "/api/v1/health/",
    "/api/v1/readiness/",
}
CONTRACT_PATH = Path(__file__).resolve().parents[3] / "contracts" / "openapi.v1.json"


def generated_schema() -> dict[str, Any]:
    schema = SchemaGenerator().get_schema(request=None, public=True)
    return json.loads(OpenApiJsonRenderer().render(schema))


def test_committed_openapi_contract_has_no_drift() -> None:
    committed = json.loads(CONTRACT_PATH.read_text())
    assert generated_schema() == committed


def test_openapi_contract_publishes_only_phase_1a_foundation() -> None:
    schema = generated_schema()
    assert set(schema["paths"]) == PHASE_1A_PATHS
    assert "staffBearerAuth" in schema["components"]["securitySchemes"]
    assert all(not path.startswith("/api/v1/members") for path in schema["paths"])


def test_openapi_documents_bff_and_staff_authentication_boundaries() -> None:
    document = generated_schema()
    login = document["paths"]["/api/v1/auth/login/"]["post"]
    me = document["paths"]["/api/v1/auth/me/"]["get"]

    assert any(parameter["name"] == "X-Gym-BFF-Secret" for parameter in login["parameters"])
    assert login.get("security", []) == []
    assert {"staffBearerAuth": []} in me["security"]
    assert document["components"]["securitySchemes"]["staffBearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
