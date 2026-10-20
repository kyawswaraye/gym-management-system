from django.urls import path

from apps.reports.export_views import (
    CashBookReportExportView,
    CheckInReportExportView,
    ClassReportExportView,
    DailyReportExportView,
    InventoryReportExportView,
    MembershipExpiryReportExportView,
    MonthlyReportExportView,
    ProductSalesReportExportView,
    ProfitLossReportExportView,
    PTSessionReportExportView,
    StockMovementReportExportView,
)
from apps.reports.views import (
    CashBookReportView,
    CheckInReportView,
    ClassReportView,
    DailyReportView,
    InventoryReportView,
    MembershipExpiryReportView,
    MonthlyReportView,
    ProductSalesReportView,
    ProfitLossReportView,
    PTSessionReportView,
    StockMovementReportView,
    TrainerDashboardView,
    TrainerSessionReportView,
)

urlpatterns = [
    path("trainer/dashboard/", TrainerDashboardView.as_view(), name="trainer-dashboard"),
    path(
        "trainer/reports/sessions/",
        TrainerSessionReportView.as_view(),
        name="trainer-session-report",
    ),
    path("reports/daily/", DailyReportView.as_view(), name="reports-daily"),
    path("reports/daily/export/", DailyReportExportView.as_view(), name="reports-daily-export"),
    path("reports/monthly/", MonthlyReportView.as_view(), name="reports-monthly"),
    path(
        "reports/monthly/export/",
        MonthlyReportExportView.as_view(),
        name="reports-monthly-export",
    ),
    path("reports/cash-book/", CashBookReportView.as_view(), name="reports-cash-book"),
    path(
        "reports/cash-book/export/",
        CashBookReportExportView.as_view(),
        name="reports-cash-book-export",
    ),
    path("reports/profit-loss/", ProfitLossReportView.as_view(), name="reports-profit-loss"),
    path(
        "reports/profit-loss/export/",
        ProfitLossReportExportView.as_view(),
        name="reports-profit-loss-export",
    ),
    path(
        "reports/membership-expiry/",
        MembershipExpiryReportView.as_view(),
        name="reports-membership-expiry",
    ),
    path(
        "reports/membership-expiry/export/",
        MembershipExpiryReportExportView.as_view(),
        name="reports-membership-expiry-export",
    ),
    path("reports/check-ins/", CheckInReportView.as_view(), name="reports-check-ins"),
    path(
        "reports/check-ins/export/",
        CheckInReportExportView.as_view(),
        name="reports-check-ins-export",
    ),
    path("reports/pt-sessions/", PTSessionReportView.as_view(), name="reports-pt-sessions"),
    path(
        "reports/pt-sessions/export/",
        PTSessionReportExportView.as_view(),
        name="reports-pt-sessions-export",
    ),
    path("reports/classes/", ClassReportView.as_view(), name="reports-classes"),
    path("reports/classes/export/", ClassReportExportView.as_view(), name="reports-classes-export"),
    path(
        "reports/product-sales/",
        ProductSalesReportView.as_view(),
        name="reports-product-sales",
    ),
    path(
        "reports/product-sales/export/",
        ProductSalesReportExportView.as_view(),
        name="reports-product-sales-export",
    ),
    path(
        "reports/stock-movements/",
        StockMovementReportView.as_view(),
        name="reports-stock-movements",
    ),
    path(
        "reports/stock-movements/export/",
        StockMovementReportExportView.as_view(),
        name="reports-stock-movements-export",
    ),
    path("reports/inventory/", InventoryReportView.as_view(), name="reports-inventory"),
    path(
        "reports/inventory/export/",
        InventoryReportExportView.as_view(),
        name="reports-inventory-export",
    ),
]
