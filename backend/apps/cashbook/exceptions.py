from apps.common.finance.exceptions import FinanceStateConflict


class CashSessionUnavailable(FinanceStateConflict):
    """The locked session cannot accept this command.

    Concurrent open-register conflicts and mutations of a session that is no
    longer open use this cacheable finance failure. Authentication,
    authorization, throttling, and serializer validation must not.
    """


class RegisterAlreadyOpen(CashSessionUnavailable):
    """One open session already exists for this gym and register."""

    default_detail = "This register already has an open session. Close it before opening another."


class CashMovementRejected(FinanceStateConflict):
    """A movement does not match an append-only cash source."""
