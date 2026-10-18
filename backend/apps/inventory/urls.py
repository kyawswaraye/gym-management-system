from django.urls import path

from apps.inventory.views import (
    ProductCategoryListCreateView,
    ProductDetailView,
    ProductListCreateView,
    ProductLowStockView,
    StockAdjustView,
    StockMovementListView,
)

urlpatterns = [
    path(
        "product-categories/",
        ProductCategoryListCreateView.as_view(),
        name="product-category-list",
    ),
    path("products/low-stock/", ProductLowStockView.as_view(), name="product-low-stock"),
    path("products/", ProductListCreateView.as_view(), name="product-list"),
    path("products/<uuid:product_id>/", ProductDetailView.as_view(), name="product-detail"),
    path("stock-movements/", StockMovementListView.as_view(), name="stock-movement-list"),
    path("stock-movements/adjust/", StockAdjustView.as_view(), name="stock-movement-adjust"),
]
