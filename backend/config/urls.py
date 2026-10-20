from django.urls import include, path

from apps.common.api.views import HealthView, ReadinessView

urlpatterns = [
    path("api/v1/health/", HealthView.as_view(), name="health"),
    path("api/v1/readiness/", ReadinessView.as_view(), name="readiness"),
    path("api/v1/auth/", include("apps.users.urls")),
    path("api/v1/member-auth/", include("apps.users.member_urls")),
    path("api/v1/", include("apps.audit.urls")),
    path("api/v1/", include("apps.tenants.urls")),
    path("api/v1/", include("apps.members.urls")),
    path("api/v1/", include("apps.files.urls")),
    path("api/v1/", include("apps.cashbook.urls")),
    path("api/v1/", include("apps.expenses.urls")),
    path("api/v1/", include("apps.salaries.urls")),
    path("api/v1/", include("apps.inventory.urls")),
    path("api/v1/", include("apps.sales.urls")),
    path("api/v1/", include("apps.trainers.urls")),
    path("api/v1/", include("apps.classes.urls")),
    path("api/v1/", include("apps.reports.urls")),
    path("api/v1/", include("apps.announcements.urls")),
    path("api/v1/", include("apps.notifications.urls")),
]
