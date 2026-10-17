from rest_framework import status

from apps.common.api.exceptions import CodedAPIException


class IdempotencyKeyReused(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "IDEMPOTENCY_KEY_REUSED"
    default_detail = "The idempotency key was already used for a different request."


class ProvisioningConflict(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "PROVISIONING_CONFLICT"
    default_detail = "The gym could not be provisioned because a unique value is already in use."


class ProvisioningValidationFailed(CodedAPIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "PROVISIONING_VALIDATION_FAILED"
    default_detail = "The provisioning request is not compatible with platform policy."


class InvalidGymTransition(CodedAPIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "INVALID_GYM_TRANSITION"
    default_detail = "The requested gym lifecycle transition is not allowed."


class OwnerCredentialRequired(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "OWNER_CREDENTIAL_REQUIRED"
    default_detail = (
        "Invitation delivery must stay enabled unless the owner already has a usable password."
    )


class OwnerWorkflowConflict(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "OWNER_WORKFLOW_CONFLICT"
    default_detail = "The Owner workflow conflicts with the current tenant state."


class InvalidInvitation(CodedAPIException):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_code = "INVALID_INVITATION"
    default_detail = "The invitation is invalid or expired."


class SettingsManagementForbidden(CodedAPIException):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "PERMISSION_DENIED"
    default_detail = "Only an Owner can manage gym settings."


class MFARequired(CodedAPIException):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "MFA_STEP_UP_REQUIRED"
    default_detail = "Recent multi-factor verification is required."


class StaffWorkflowConflict(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "STAFF_WORKFLOW_CONFLICT"
    default_detail = "The staff workflow conflicts with the current tenant state."


class StaffInvitationInvalid(CodedAPIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "STAFF_INVITATION_INVALID"
    default_detail = "The staff invitation is not valid."


class StaffDelegationDenied(CodedAPIException):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "DELEGATION_DENIED"
    default_detail = "The caller cannot assign the requested roles."


class RoleWorkflowConflict(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "ROLE_WORKFLOW_CONFLICT"
    default_detail = "The role change conflicts with the current tenant state."


class RoleAssignmentInvalid(CodedAPIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "ROLE_ASSIGNMENT_INVALID"
    default_detail = "One or more roles are unavailable."


class InvalidRolePermissions(CodedAPIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "ROLE_PERMISSION_INVALID"
    default_detail = "One or more permissions are unavailable."


class SubscriptionPlanInUse(CodedAPIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "SUBSCRIPTION_PLAN_IN_USE"
    default_detail = "This plan cannot be retired while a gym has a trial or active subscription."
