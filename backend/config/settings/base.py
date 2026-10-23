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
    "apps.announcements",
    "apps.notifications",
    "apps.files",
    "apps.cashbook",
    "apps.expenses",
    "apps.salaries",
    "apps.inventory",
    "apps.sales",
    "apps.trainers",
    "apps.classes",
    "apps.reports",
]
MIDDLEWARE = [
    "apps.common.observability.request_log.RequestLogMiddleware",
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
    "DEFAULT_THROTTLE_RATES": {
        "anon": "30/min",
        "user": "120/min",
        "gym_selection_issue": "5/min",
        "member_login": "10/min",
        "member_login_ip": "30/min",
        "member_refresh": "30/min",
        "member_refresh_ip": "30/min",
        "member_logout": "30/min",
        "member_logout_ip": "30/min",
        "member_check_in_scan": "30/min",
        "trainer_check_in_scan": "30/min",
        "trainer_check_in_scan_ip": "30/min",
        "member_self_read": "60/min",
        "member_self_write": "20/min",
        "member_file_write": "10/min",
        "member_file_download": "30/min",
        "device_challenge_ip": "60/min",
        "device_challenge_selector": "20/min",
        "device_ingest_ip": "60/min",
        "device_ingest_selector": "20/min",
        "gym_selection_select": "10/min",
        "file_upload_intent": "10/300",
        "file_complete": "20/300",
        "file_download_url": "60/60",
        "file_delete": "10/300",
        "cashbook_command": "120/60",
        "payments_command": "120/60",
        "expenses_command": "120/60",
        "salaries_command": "120/60",
        "inventory_command": "120/60",
        "retail_command": "120/60",
        "trainer_assignment_command": "120/60",
        "service_package_command": "120/60",
        "pt_sale_command": "120/60",
        "pt_commission_command": "120/60",
        "member_package_command": "120/60",
        "class_command": "120/60",
        "announcement_command": "120/60",
        "booking_command": "120/60",
        "password_forgot": "5/min",
        "password_forgot_ip": "20/min",
        "password_reset": "10/min",
        "password_reset_ip": "20/min",
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Gym Management Platform API",
    "DESCRIPTION": (
        "Implemented Phase 1A foundation, Phase 1B platform access, MembershipPlan, "
        "and Member CRUD/archive contracts."
    ),
    "VERSION": "1.0.0",
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    "COMPONENT_SPLIT_REQUEST": True,
    "ENUM_NAME_OVERRIDES": {
        "SubscriptionPlanStatusEnum": "apps.tenants.models.SubscriptionPlan.Status",
        "PaymentMethodEnum": "apps.members.models.Payment.Method",
        "MemberStatusEnum": "apps.members.models.Member.Status",
        "MembershipStatusEnum": "apps.members.models.Membership.Status",
        "KindEnum": ["NFC", "BIOMETRIC"],
        "AnnouncementTypeEnum": [
            ("EVENT", "EVENT"),
            ("PROMOTION", "PROMOTION"),
            ("GENERAL", "GENERAL"),
        ],
        "MemberNotificationTypeEnum": [
            ("MEMBERSHIP_EXPIRY", "MEMBERSHIP_EXPIRY"),
            ("ANNOUNCEMENT", "ANNOUNCEMENT"),
        ],
        "MemberScheduleBookingKindEnum": ["PT", "CLASS"],
        # MembershipPlan.DurationUnit and ServicePackage.ValidityUnit are the same
        # DAY/WEEK/MONTH/YEAR calendar set, so they share one schema component.
        "ValidityUnitEnum": "apps.members.models.MembershipPlan.DurationUnit",
    },
    "PREPROCESSING_HOOKS": ["apps.common.api.schema.phase_1a_endpoints"],
}

JWT_ISSUER = os.environ.get("JWT_ISSUER", "gym-platform-api")
JWT_STAFF_AUDIENCE = os.environ.get("JWT_STAFF_AUDIENCE", "gym-platform")
JWT_PLATFORM_AUDIENCE = os.environ.get("JWT_PLATFORM_AUDIENCE", "gym-platform-admin")
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
MFA_TOKEN_PEPPER = os.environ.get("MFA_TOKEN_PEPPER", REFRESH_TOKEN_PEPPER)
MFA_ENCRYPTION_KEY = os.environ.get("MFA_ENCRYPTION_KEY", "")
FINANCE_IDEMPOTENCY_ENCRYPTION_KEY = os.environ.get("FINANCE_IDEMPOTENCY_ENCRYPTION_KEY", "")
FINANCE_IDEMPOTENCY_MAX_RESPONSE_BYTES = 64 * 1024
FINANCE_IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60
FINANCE_IDEMPOTENCY_CLAIM_SECONDS = 30
MFA_ISSUER = os.environ.get("MFA_ISSUER", "Gym Management System")
MFA_CHALLENGE_TTL_SECONDS = int(os.environ.get("MFA_CHALLENGE_TTL_SECONDS", "300"))
MFA_CHALLENGE_MAX_ATTEMPTS = int(os.environ.get("MFA_CHALLENGE_MAX_ATTEMPTS", "5"))
MFA_ENROLLMENT_TTL_SECONDS = int(os.environ.get("MFA_ENROLLMENT_TTL_SECONDS", "600"))
MFA_STEP_UP_TTL_SECONDS = int(os.environ.get("MFA_STEP_UP_TTL_SECONDS", "600"))
INVITATION_TOKEN_PEPPER = os.environ.get("INVITATION_TOKEN_PEPPER", REFRESH_TOKEN_PEPPER)
INVITATION_TTL_SECONDS = int(os.environ.get("INVITATION_TTL_SECONDS", str(48 * 60 * 60)))
PASSWORD_RESET_TOKEN_PEPPER = os.environ.get("PASSWORD_RESET_TOKEN_PEPPER", "")
PUSH_BACKEND = os.environ.get("PUSH_BACKEND", "locmem")
PUSH_TOKEN_ENCRYPTION_KEY = os.environ.get("PUSH_TOKEN_ENCRYPTION_KEY", "")
PUSH_TOKEN_FINGERPRINT_PEPPER = os.environ.get("PUSH_TOKEN_FINGERPRINT_PEPPER", "")
FCM_PROJECT_ID = os.environ.get("FCM_PROJECT_ID", "")
FCM_SERVICE_ACCOUNT_JSON = os.environ.get("FCM_SERVICE_ACCOUNT_JSON", "")
PASSWORD_RESET_TTL_SECONDS = int(os.environ.get("PASSWORD_RESET_TTL_SECONDS", str(30 * 60)))
APP_PUBLIC_URL = os.environ.get("APP_PUBLIC_URL", "http://localhost:3000")
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_TIMEOUT_SECONDS = int(os.environ.get("RESEND_TIMEOUT_SECONDS", "10"))
RESEND_MAX_RESPONSE_BYTES = int(os.environ.get("RESEND_MAX_RESPONSE_BYTES", "4096"))
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "no-reply@localhost")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "25"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "").lower() == "true"
BFF_SHARED_SECRET = os.environ.get("BFF_SHARED_SECRET", "")
BFF_SECRET_HEADER = "X-Gym-BFF-Secret"
DJANGO_LISTEN_HOST = os.environ.get("DJANGO_LISTEN_HOST", "127.0.0.1")
GYM_SELECTION_TICKET_TTL_SECONDS = 300
GYM_SELECTION_TICKET_MAX_ATTEMPTS = 5
GYM_SELECTION_TICKET_RETENTION_DAYS = 30
REQUEST_ID_MAX_LENGTH = 128
STRUCTURED_REQUEST_LOG = False
MINIO_ENDPOINT_URL = os.environ.get("MINIO_ENDPOINT_URL", "")
MINIO_PUBLIC_URL = os.environ.get("MINIO_PUBLIC_URL", "")
MINIO_BUCKET = os.environ.get("MINIO_BUCKET", "")
MINIO_REGION = os.environ.get("MINIO_REGION", "us-east-1")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "")
MINIO_SESSION_TOKEN = os.environ.get("MINIO_SESSION_TOKEN", "")
MINIO_ADDRESSING_STYLE = os.environ.get("MINIO_ADDRESSING_STYLE", "path")
MINIO_TIMEOUT_SECONDS = int(os.environ.get("MINIO_TIMEOUT_SECONDS", "5"))
OBJECT_STORE_BUCKET_ALLOWLIST = tuple(
    item.strip()
    for item in os.environ.get("OBJECT_STORE_BUCKET_ALLOWLIST", "").split(",")
    if item.strip()
)
OBJECT_STORE_ORIGIN_ALLOWLIST = tuple(
    item.strip()
    for item in os.environ.get("OBJECT_STORE_ORIGIN_ALLOWLIST", "").split(",")
    if item.strip()
)
CLAMAV_HOST = os.environ.get("CLAMAV_HOST", "")
CLAMAV_PORT = int(os.environ.get("CLAMAV_PORT", "3310"))
CLAMAV_TIMEOUT_SECONDS = int(os.environ.get("CLAMAV_TIMEOUT_SECONDS", "15"))
FILE_OBJECT_STORE = os.environ.get("FILE_OBJECT_STORE", "")
FILE_SCANNER = os.environ.get("FILE_SCANNER", "")
FILE_IMAGE_PROCESSOR = os.environ.get("FILE_IMAGE_PROCESSOR", "")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "gym-platform",
    }
}
