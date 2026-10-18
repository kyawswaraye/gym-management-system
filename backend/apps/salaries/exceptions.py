from rest_framework import status

from apps.common.finance.exceptions import CacheableIdempotentFailure


class SalaryPostingConflict(CacheableIdempotentFailure):
    status_code = status.HTTP_409_CONFLICT
    default_code = "FINANCE_STATE_CONFLICT"
    default_detail = "The salary period cannot accept this command."
