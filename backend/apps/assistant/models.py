from __future__ import annotations

from django.db import models

from apps.common.db.models import TenantModel


def empty_dict() -> dict[str, object]:
    return {}


def empty_list() -> list[object]:
    return []


class Conversation(TenantModel):
    """One staff chat. Rows are gym-scoped and are not a business transaction."""

    class Status(models.TextChoices):
        OPEN = "open"
        CLOSED = "closed"

    created_by_membership = models.ForeignKey(
        "tenants.GymMembership",
        on_delete=models.RESTRICT,
        related_name="assistant_conversations",
    )
    title = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=8, choices=Status, default=Status.OPEN)

    class Meta:
        db_table = "assistant_conversation"
        constraints = [
            models.UniqueConstraint(
                fields=["gym", "id"],
                name="assistant_conversation_gym_id_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["open", "closed"]),
                name="assistant_conversation_status_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "created_at"], name="assistant_conv_gym_created_idx"),
        ]


class Goal(TenantModel):
    """Persistent staff request. Version changes only with a committed effect."""

    class Status(models.TextChoices):
        COLLECTING = "collecting"
        READY = "ready"
        EXECUTING = "executing"
        WAITING_CONFIRMATION = "waiting_confirmation"
        WAITING_APPROVAL = "waiting_approval"
        PAUSED = "paused"
        FAILED = "failed"
        COMPLETED = "completed"
        CANCELLED = "cancelled"
        EXPIRED = "expired"

    OPEN_STATUSES = (
        Status.COLLECTING,
        Status.READY,
        Status.EXECUTING,
        Status.WAITING_CONFIRMATION,
        Status.WAITING_APPROVAL,
        Status.PAUSED,
    )
    TERMINAL_STATUSES = (
        Status.FAILED,
        Status.COMPLETED,
        Status.CANCELLED,
        Status.EXPIRED,
    )

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.RESTRICT,
        related_name="goals",
    )
    created_by_membership = models.ForeignKey(
        "tenants.GymMembership",
        on_delete=models.RESTRICT,
        related_name="assistant_goals",
    )
    intent = models.CharField(max_length=200)
    status = models.CharField(max_length=24, choices=Status, default=Status.COLLECTING)
    is_active = models.BooleanField(default=True)
    collected_information = models.JSONField(default=empty_dict)
    missing_information = models.JSONField(default=empty_list)
    workflow_plan = models.JSONField(default=empty_dict)
    result_summary = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "assistant_goal"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="assistant_goal_gym_id_uniq"),
            models.UniqueConstraint(
                fields=["gym", "conversation"],
                condition=models.Q(is_active=True),
                name="assistant_goal_one_active_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1),
                name="assistant_goal_version_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=[
                        "collecting",
                        "ready",
                        "executing",
                        "waiting_confirmation",
                        "waiting_approval",
                        "paused",
                        "failed",
                        "completed",
                        "cancelled",
                        "expired",
                    ]
                ),
                name="assistant_goal_status_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        is_active=True,
                        status__in=[
                            "collecting",
                            "ready",
                            "executing",
                            "waiting_confirmation",
                            "waiting_approval",
                            "paused",
                        ],
                    )
                    | models.Q(
                        is_active=False,
                        status__in=["failed", "completed", "cancelled", "expired"],
                    )
                ),
                name="assistant_goal_activity_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["gym", "conversation", "created_at"],
                name="assistant_goal_conv_idx",
            ),
        ]


class Message(TenantModel):
    class Role(models.TextChoices):
        STAFF = "staff"
        ASSISTANT = "assistant"
        SYSTEM = "system"

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.RESTRICT,
        related_name="messages",
    )
    goal = models.ForeignKey(
        Goal,
        on_delete=models.RESTRICT,
        related_name="messages",
        null=True,
        blank=True,
    )
    author_membership = models.ForeignKey(
        "tenants.GymMembership",
        on_delete=models.RESTRICT,
        related_name="assistant_messages",
        null=True,
        blank=True,
    )
    role = models.CharField(max_length=16, choices=Role)
    body = models.TextField()

    class Meta:
        db_table = "assistant_message"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="assistant_message_gym_id_uniq"),
            models.CheckConstraint(
                condition=models.Q(role__in=["staff", "assistant", "system"]),
                name="assistant_message_role_ck",
            ),
            models.CheckConstraint(
                condition=~models.Q(body=""),
                name="assistant_message_body_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["gym", "conversation", "created_at"],
                name="assistant_message_conv_idx",
            ),
        ]


class Workflow(TenantModel):
    goal = models.OneToOneField(Goal, on_delete=models.RESTRICT, related_name="workflow")
    status = models.CharField(max_length=24, default=Goal.Status.COLLECTING)

    class Meta:
        db_table = "assistant_workflow"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="assistant_workflow_gym_id_uniq"),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=[
                        "collecting",
                        "ready",
                        "executing",
                        "waiting_confirmation",
                        "waiting_approval",
                        "paused",
                        "failed",
                        "completed",
                        "cancelled",
                        "expired",
                    ]
                ),
                name="assistant_workflow_status_ck",
            ),
        ]


class WorkflowStep(TenantModel):
    class Status(models.TextChoices):
        PENDING = "pending"
        COLLECTING = "collecting"
        READY = "ready"
        EXECUTING = "executing"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        SKIPPED = "skipped"
        WAITING_CONFIRMATION = "waiting_confirmation"
        WAITING_APPROVAL = "waiting_approval"
        CANCELLED = "cancelled"

    workflow = models.ForeignKey(Workflow, on_delete=models.RESTRICT, related_name="steps")
    goal = models.ForeignKey(Goal, on_delete=models.RESTRICT, related_name="steps")
    position = models.PositiveIntegerField()
    tool_name = models.CharField(max_length=64)
    status = models.CharField(max_length=24, choices=Status, default=Status.PENDING)
    attempt_number = models.PositiveIntegerField(default=1)
    depends_on = models.JSONField(default=empty_list)
    required_input = models.JSONField(default=empty_dict)
    error_code = models.CharField(max_length=64, blank=True)
    error_summary = models.CharField(max_length=400, blank=True)

    class Meta:
        db_table = "assistant_workflow_step"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="assistant_step_gym_id_uniq"),
            models.UniqueConstraint(
                fields=["workflow", "position"],
                name="assistant_step_position_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(position__gte=1) & models.Q(attempt_number__gte=1),
                name="assistant_step_numbers_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(tool_name__regex=r"^[a-z][a-z0-9_]{0,63}$"),
                name="assistant_step_tool_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=[
                        "pending",
                        "collecting",
                        "ready",
                        "executing",
                        "succeeded",
                        "failed",
                        "skipped",
                        "waiting_confirmation",
                        "waiting_approval",
                        "cancelled",
                    ]
                ),
                name="assistant_step_status_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "goal", "position"], name="assistant_step_goal_idx"),
        ]


class ToolExecution(TenantModel):
    """Committed claim for one execution identity.

    The row is inserted in the same transaction as its database effect. A
    rollback leaves no row. A deferred trigger rejects an empty effect
    reference when that transaction commits. External mail, object storage,
    and provider calls are recorded as uncovered and are not part of this claim.
    """

    goal = models.ForeignKey(Goal, on_delete=models.RESTRICT, related_name="executions")
    step = models.ForeignKey(WorkflowStep, on_delete=models.RESTRICT, related_name="executions")
    tool_name = models.CharField(max_length=64)
    attempt_number = models.PositiveIntegerField()
    execution_identity = models.CharField(max_length=64)
    payload_hash = models.CharField(max_length=64)
    effect_reference = models.CharField(max_length=64, blank=True)
    external_side_effects = models.JSONField(default=empty_list)
    covers_external_side_effects = models.BooleanField(default=False)

    class Meta:
        db_table = "assistant_tool_execution"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="assistant_execution_gym_id_uniq"),
            models.UniqueConstraint(
                fields=["gym", "execution_identity"],
                name="assistant_execution_identity_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(attempt_number__gte=1),
                name="assistant_execution_attempt_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(execution_identity__regex=r"^[0-9a-f]{64}$")
                & models.Q(payload_hash__regex=r"^[0-9a-f]{64}$"),
                name="assistant_execution_hash_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(tool_name__regex=r"^[a-z][a-z0-9_]{0,63}$"),
                name="assistant_execution_tool_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(covers_external_side_effects=False),
                name="assistant_execution_external_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "goal", "created_at"], name="assistant_execution_goal_idx"),
        ]


class AttachmentReference(TenantModel):
    """Points at an existing file asset. It does not upload or scan the object."""

    goal = models.ForeignKey(Goal, on_delete=models.RESTRICT, related_name="attachments")
    file_asset = models.ForeignKey(
        "files.FileAsset",
        on_delete=models.RESTRICT,
        related_name="assistant_references",
    )
    purpose = models.CharField(max_length=64)

    class Meta:
        db_table = "assistant_attachment_reference"
        constraints = [
            models.UniqueConstraint(
                fields=["gym", "id"],
                name="assistant_attachment_gym_id_uniq",
            ),
            models.UniqueConstraint(
                fields=["goal", "file_asset"],
                name="assistant_attachment_target_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(purpose__regex=r"^[a-z][a-z0-9_]{0,63}$"),
                name="assistant_attachment_purpose_ck",
            ),
        ]
