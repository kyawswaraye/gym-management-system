from django.urls import path

from apps.cashbook.views import (
    AdjustCashSessionView,
    CashSessionDetailView,
    CashSessionListView,
    CashTransactionListView,
    OpenCashSessionView,
    ReconcileCashSessionView,
)

urlpatterns = [
    path("cash-sessions/", CashSessionListView.as_view(), name="cash-sessions"),
    path("cash-sessions/open/", OpenCashSessionView.as_view(), name="cash-sessions-open"),
    path("cash-sessions/<uuid:id>/", CashSessionDetailView.as_view(), name="cash-session-detail"),
    path(
        "cash-sessions/<uuid:id>/adjustments/",
        AdjustCashSessionView.as_view(),
        name="cash-session-adjustments",
    ),
    path(
        "cash-sessions/<uuid:id>/reconcile/",
        ReconcileCashSessionView.as_view(),
        name="cash-session-reconcile",
    ),
    path("cash-transactions/", CashTransactionListView.as_view(), name="cash-transactions"),
]
