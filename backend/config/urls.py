from django.urls import include, path

from apps.common.api.views import HealthView, ReadinessView

urlpatterns = [
    path("api/v1/health/", HealthView.as_view(), name="health"),
    path("api/v1/readiness/", ReadinessView.as_view(), name="readiness"),
    path("api/v1/auth/", include("apps.users.urls")),
    path("api/v1/", include("apps.audit.urls")),
    path("api/v1/", include("apps.tenants.urls")),
    path("api/v1/", include("apps.members.urls")),
]
