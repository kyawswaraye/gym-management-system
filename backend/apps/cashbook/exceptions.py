from apps.common.finance.exceptions import FinanceStateConflict


class CashSessionUnavailable(FinanceStateConflict):
    """The locked session cannot accept this command.

    Concurrent open-register conflicts and mutations of a session that is no
    longer open use this cacheable finance failure. Authentication,
    authorization, throttling, and serializer validation must not.
    """


class CashMovementRejected(FinanceStateConflict):
    """A movement would break the session's non-negative sales or expenses."""
