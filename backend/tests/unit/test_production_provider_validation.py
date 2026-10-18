from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from apps.common.production import (
    PRODUCTION_SCHEDULER_COMMANDS,
    RESEND_BACKEND,
    require_allowlisted_value,
    require_aws_singapore_region,
    require_dedicated_reset_pepper,
    require_https_endpoint,
    require_https_public_url,
    require_private_clamav_host,
    require_resend_api_key,
    require_resend_backend,
    require_reset_ttl_seconds,
    require_scheduler_commands,
    require_verified_sender,
)


def test_production_mail_requires_resend_and_a_verified_sender() -> None:
    require_resend_backend(RESEND_BACKEND)
    require_resend_api_key("re_live_key_that_is_long")
    require_verified_sender("Gym <mail@gym.example>")
    require_https_public_url("https://app.gym.example")
    require_reset_ttl_seconds(1800)
    require_scheduler_commands(PRODUCTION_SCHEDULER_COMMANDS)
    with pytest.raises(ImproperlyConfigured):
        require_resend_backend("django.core.mail.backends.smtp.EmailBackend")
    with pytest.raises(ImproperlyConfigured):
        require_verified_sender("no-reply@localhost")
    with pytest.raises(ImproperlyConfigured):
        require_https_public_url("http://app.gym.example")


def test_reset_pepper_must_be_dedicated() -> None:
    require_dedicated_reset_pepper(
        reset_pepper="reset-pepper-value-that-is-long-enough",
        other_secrets={"REFRESH_TOKEN_PEPPER": "refresh-pepper-value-that-is-long"},
    )
    with pytest.raises(ImproperlyConfigured):
        require_dedicated_reset_pepper(
            reset_pepper="shared-pepper-value-that-is-long-enough",
            other_secrets={"REFRESH_TOKEN_PEPPER": "shared-pepper-value-that-is-long-enough"},
        )


def test_production_s3_and_clamav_bounds() -> None:
    require_https_endpoint("https://s3.ap-southeast-1.amazonaws.com", name="MINIO_ENDPOINT_URL")
    require_aws_singapore_region("ap-southeast-1")
    require_allowlisted_value("gym-prod", ("gym-prod",), name="MINIO_BUCKET")
    require_private_clamav_host("clamav")
    with pytest.raises(ImproperlyConfigured):
        require_https_endpoint("http://minio:9000", name="MINIO_ENDPOINT_URL")
    with pytest.raises(ImproperlyConfigured):
        require_aws_singapore_region("us-east-1")
    with pytest.raises(ImproperlyConfigured):
        require_private_clamav_host("0.0.0.0")
