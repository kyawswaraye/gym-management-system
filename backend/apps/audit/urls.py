from django.urls import path

from apps.audit.views import PlatformAuditListView, TenantAuditListView

urlpatterns = [
    path("admin/audit-logs/", PlatformAuditListView.as_view(), name="admin-audit-logs"),
    path("audit-logs/", TenantAuditListView.as_view(), name="audit-logs"),
]
