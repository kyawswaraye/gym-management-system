from rest_framework import status

from apps.common.finance.exceptions import CacheableIdempotentFailure


class ProtectedExpenseCategory(CacheableIdempotentFailure):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "PROTECTED_EXPENSE_CATEGORY"
    default_detail = "This expense category cannot be used for a manual expense."


class ExpensePostingConflict(CacheableIdempotentFailure):
    status_code = status.HTTP_409_CONFLICT
    default_code = "FINANCE_STATE_CONFLICT"
    default_detail = "The expense cannot accept this command."
