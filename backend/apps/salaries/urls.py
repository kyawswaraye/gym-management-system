from django.urls import path

from apps.salaries.views import (
    SalaryPeriodApproveView,
    SalaryPeriodCancelView,
    SalaryPeriodDetailView,
    SalaryPeriodLinesView,
    SalaryPeriodListCreateView,
    SalaryPeriodRejectView,
    SalaryPeriodSubmitView,
    SalaryPeriodVoidView,
)

urlpatterns = [
    path("salary-periods/", SalaryPeriodListCreateView.as_view(), name="salary-period-list"),
    path(
        "salary-periods/<uuid:period_id>/",
        SalaryPeriodDetailView.as_view(),
        name="salary-period-detail",
    ),
    path(
        "salary-periods/<uuid:period_id>/lines/",
        SalaryPeriodLinesView.as_view(),
        name="salary-period-lines",
    ),
    path(
        "salary-periods/<uuid:period_id>/submit/",
        SalaryPeriodSubmitView.as_view(),
        name="salary-period-submit",
    ),
    path(
        "salary-periods/<uuid:period_id>/approve/",
        SalaryPeriodApproveView.as_view(),
        name="salary-period-approve",
    ),
    path(
        "salary-periods/<uuid:period_id>/void/",
        SalaryPeriodVoidView.as_view(),
        name="salary-period-void",
    ),
    path(
        "salary-periods/<uuid:period_id>/reject/",
        SalaryPeriodRejectView.as_view(),
        name="salary-period-reject",
    ),
    path(
        "salary-periods/<uuid:period_id>/cancel/",
        SalaryPeriodCancelView.as_view(),
        name="salary-period-cancel",
    ),
]
