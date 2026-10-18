from django.urls import path

from apps.expenses.views import (
    ExpenseApproveView,
    ExpenseCancelView,
    ExpenseCategoryDetailView,
    ExpenseCategoryListCreateView,
    ExpenseDetailView,
    ExpenseListCreateView,
    ExpenseRejectView,
    ExpenseSubmitView,
)

urlpatterns = [
    path("expense-categories/", ExpenseCategoryListCreateView.as_view(), name="expense-categories"),
    path(
        "expense-categories/<uuid:category_id>/",
        ExpenseCategoryDetailView.as_view(),
        name="expense-category-detail",
    ),
    path("expenses/", ExpenseListCreateView.as_view(), name="expense-list"),
    path("expenses/<uuid:expense_id>/", ExpenseDetailView.as_view(), name="expense-detail"),
    path(
        "expenses/<uuid:expense_id>/submit/",
        ExpenseSubmitView.as_view(),
        name="expense-submit",
    ),
    path(
        "expenses/<uuid:expense_id>/approve/",
        ExpenseApproveView.as_view(),
        name="expense-approve",
    ),
    path(
        "expenses/<uuid:expense_id>/reject/",
        ExpenseRejectView.as_view(),
        name="expense-reject",
    ),
    path(
        "expenses/<uuid:expense_id>/cancel/",
        ExpenseCancelView.as_view(),
        name="expense-cancel",
    ),
]
