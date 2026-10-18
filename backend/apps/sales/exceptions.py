from apps.common.finance.exceptions import FinanceStateConflict


class RetailPostingConflict(FinanceStateConflict):
    """The locked retail sale can no longer accept this command."""
