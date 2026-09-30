from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "unsafe-local-only")
DEBUG = False
ALLOWED_HOSTS = [
    value for value in os.environ.get("ALLOWED_HOSTS", "localhost").split(",") if value
]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "rest_framework",
    "drf_spectacular",
    "apps.users",
    "apps.tenants",
    "apps.audit",
    "apps.members",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.common.api.middleware.RequestIDMiddleware",
    "apps.common.api.bff.BffTrustMiddleware",
    "django.middleware.common.CommonMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES: list[dict[str, object]] = []
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

_database_mode = os.environ.get("DATABASE_ROLE_MODE", "runtime").upper()
_database_prefix = "MIGRATION_DB" if _database_mode == "MIGRATION" else "RUNTIME_DB"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "gym_platform"),
        "USER": os.environ.get(f"{_database_prefix}_USER", "gym_runtime"),
        "PASSWORD": os.environ.get(f"{_database_prefix}_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        "CONN_MAX_AGE": int(os.environ.get("DB_CONN_MAX_AGE", "0")),
        "ATOMIC_REQUESTS": True,
        "OPTIONS": {"connect_timeout": int(os.environ.get("DB_CONNECT_TIMEOUT", "5"))},
    }
}

AUTH_USER_MODEL = "users.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.PBKDF2PasswordHasher"]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.users.authentication.StaffJWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.common.api.exceptions.api_exception_handler",
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_THROTTLE_RATES": {"anon": "30/min", "user": "120/min"},
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Gym Management Platform API",
    "DESCRIPTION": "Implemented Phase 1A foundation API contract.",
    "VERSION": "1.0.0",
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    "COMPONENT_SPLIT_REQUEST": True,
    "PREPROCESSING_HOOKS": ["apps.common.api.schema.phase_1a_endpoints"],
}

JWT_ISSUER = os.environ.get("JWT_ISSUER", "gym-platform-api")
JWT_STAFF_AUDIENCE = os.environ.get("JWT_STAFF_AUDIENCE", "gym-platform")
JWT_MEMBER_AUDIENCE = os.environ.get("JWT_MEMBER_AUDIENCE", "gym-platform-member")
JWT_KEY_ID = os.environ.get("JWT_KEY_ID", "")
JWT_PRIVATE_KEY = os.environ.get("JWT_PRIVATE_KEY", "").replace("\\n", "\n")
JWT_PUBLIC_KEYS = {
    key: value.replace("\\n", "\n")
    for item in os.environ.get("JWT_PUBLIC_KEYS", "").split(";;")
    if item and "=" in item
    for key, value in [item.split("=", 1)]
}
JWT_ACCESS_TTL_SECONDS = 600
JWT_CLOCK_SKEW_SECONDS = int(os.environ.get("JWT_CLOCK_SKEW_SECONDS", "30"))
REFRESH_TTL_SECONDS = int(os.environ.get("REFRESH_TTL_SECONDS", str(30 * 24 * 60 * 60)))
REFRESH_TOKEN_PEPPER = os.environ.get("REFRESH_TOKEN_PEPPER", "")
BFF_SHARED_SECRET = os.environ.get("BFF_SHARED_SECRET", "")
BFF_SECRET_HEADER = "X-Gym-BFF-Secret"
DJANGO_LISTEN_HOST = os.environ.get("DJANGO_LISTEN_HOST", "127.0.0.1")
GYM_SELECTION_TICKET_TTL_SECONDS = 300
GYM_SELECTION_TICKET_MAX_ATTEMPTS = 5
REQUEST_ID_MAX_LENGTH = 128
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "gym-platform",
    }
}
