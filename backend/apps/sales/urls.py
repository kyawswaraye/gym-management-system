from django.urls import path

from apps.sales.views import (
    RetailSaleApproveView,
    RetailSaleCancelView,
    RetailSaleDetailView,
    RetailSaleListCreateView,
    RetailSaleRejectView,
    RetailSaleSubmitView,
    RetailSaleVoidView,
)

urlpatterns = [
    path("retail-sales/", RetailSaleListCreateView.as_view(), name="retail-sales"),
    path("retail-sales/<uuid:sale_id>/", RetailSaleDetailView.as_view(), name="retail-sale-detail"),
    path(
        "retail-sales/<uuid:sale_id>/submit/",
        RetailSaleSubmitView.as_view(),
        name="retail-sale-submit",
    ),
    path(
        "retail-sales/<uuid:sale_id>/approve/",
        RetailSaleApproveView.as_view(),
        name="retail-sale-approve",
    ),
    path(
        "retail-sales/<uuid:sale_id>/void/",
        RetailSaleVoidView.as_view(),
        name="retail-sale-void",
    ),
    path(
        "retail-sales/<uuid:sale_id>/reject/",
        RetailSaleRejectView.as_view(),
        name="retail-sale-reject",
    ),
    path(
        "retail-sales/<uuid:sale_id>/cancel/",
        RetailSaleCancelView.as_view(),
        name="retail-sale-cancel",
    ),
]
