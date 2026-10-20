from rest_framework import status

from apps.common.api.exceptions import CodedAPIException
from apps.common.finance.exceptions import CacheableIdempotentFailure


class AssignmentOverlap(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "ASSIGNMENT_OVERLAP"
    default_detail = "The assignment dates overlap another active assignment."


class AssignmentStateConflict(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "ASSIGNMENT_STATE_CONFLICT"
    default_detail = "The assignment cannot change from its current state."


class TrainerNotEligible(CodedAPIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "TRAINER_NOT_ELIGIBLE"
    default_detail = "The staff profile is not an active trainer."


class MemberNotActive(CodedAPIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "MEMBER_NOT_ACTIVE"
    default_detail = "The member is not active."


class ServicePackageConflict(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "SERVICE_PACKAGE_CONFLICT"
    default_detail = "The service package conflicts with an existing package."

    def __init__(self, field: str) -> None:
        self.error_fields = {field: ["This value is already in use."]}
        super().__init__()


class ClassCatalogUnavailable(CodedAPIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "CLASS_CATALOG_UNAVAILABLE"
    default_detail = "The class catalog is not published."


class PaymentDraftUnavailable(CodedAPIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "PAYMENT_DRAFT_UNPUBLISHED"
    default_detail = "The payment draft is not published."


class AssignmentNotEffective(CodedAPIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "ASSIGNMENT_NOT_EFFECTIVE"
    default_detail = "The trainer assignment is not effective on the service date."


class SaleCurrencyMismatch(CodedAPIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "CURRENCY_MISMATCH"
    default_detail = "The currency must match the gym currency."


class PTSaleStateConflict(CacheableIdempotentFailure):
    status_code = status.HTTP_409_CONFLICT
    default_code = "PT_SALE_STATE_CONFLICT"
    default_detail = "The PT sale cannot accept this command."


class CommissionOverpay(CacheableIdempotentFailure):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "COMMISSION_OVERPAY"
    default_detail = "Amount exceeds the outstanding commission."


class CommissionPayoutConflict(CacheableIdempotentFailure):
    status_code = status.HTTP_409_CONFLICT
    default_code = "COMMISSION_PAYOUT_CONFLICT"
    default_detail = "The commission payout cannot accept this command."


class PackageNotEligible(CacheableIdempotentFailure):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "PACKAGE_NOT_ELIGIBLE"
    default_detail = "The service package is not eligible for this sale."


class SaleGrantRequired(CacheableIdempotentFailure):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "SALE_GRANT_REQUIRED"
    default_detail = "A PT package is granted by an approved sale."


class LedgerBounds(CacheableIdempotentFailure):
    status_code = status.HTTP_409_CONFLICT
    default_code = "LEDGER_BOUNDS"
    default_detail = "The session balance would leave the granted range."


class PayoutReversalIncomplete(CacheableIdempotentFailure):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "PAYOUT_REVERSAL_INCOMPLETE"
    default_detail = "Name every posted payout once, and only those payouts."


class PackageConsumed(CacheableIdempotentFailure):
    status_code = status.HTTP_409_CONFLICT
    default_code = "PACKAGE_CONSUMED"
    default_detail = "The package has been consumed and cannot be voided with the sale."


class LedgerEntryIneligible(CacheableIdempotentFailure):
    status_code = status.HTTP_409_CONFLICT
    default_code = "LEDGER_ENTRY_INELIGIBLE"
    default_detail = "That ledger entry cannot be corrected."
