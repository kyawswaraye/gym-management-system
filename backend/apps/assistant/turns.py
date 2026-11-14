"""One authenticated assistant turn. Writes wait for a separate confirmation."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import date, timedelta
from time import monotonic
from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.assistant.access import assistant_full_access
from apps.assistant.configuration import (
    assistant_configuration,
    max_execution_seconds,
    max_steps,
    max_tool_calls,
)
from apps.assistant.dispatch import (
    approval_source,
    execute_confirmed_write,
    execute_offered_read,
    tool_contracts,
)
from apps.assistant.entitlement import require_gym_ai
from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import ToolContract
from apps.assistant.knowledge import (
    assistant_context,
    gate_sentence,
    grounded_information_reply,
    grounded_read_reply,
    public_activity,
    visible_reply,
)
from apps.assistant.lookups import (
    assignment_gap,
    attach_sale_package,
    booking_package_gap,
    existing_assignment,
    member_has_class_booking,
    membership_conflict,
    proposal_block,
    refundable_balance,
    remember_sale_package,
    resolve_references,
    sale_package_failure,
    unmetered_gate,
)
from apps.assistant.models import Conversation, Goal, Message, WorkflowStep
from apps.assistant.prerequisites import missing_fields
from apps.assistant.proposals import (
    declaration,
    model_arguments,
    public_pending,
    summary_for,
    with_snapshot,
)
from apps.assistant.provider import ProviderCallError, generate_content
from apps.assistant.queries import get_conversation, list_messages
from apps.assistant.records import (
    append_message,
    close_goal,
    open_confirmation,
    open_conversation,
    stored_payload,
)
from apps.assistant.registry import ToolDefinition, production_registry
from apps.assistant.serializers import message_data
from apps.assistant.workflow_state import (
    absorb_proposal,
    absorb_read,
    completion_sentence,
    confirmation_reply,
    is_state_message,
    load_state,
    note_verified,
    provider_notice,
    remember_commit,
    save_state,
    state_brief,
)
from apps.classes.exceptions import ClassFull, ScheduleConflict
from apps.common.api.exceptions import CodedAPIException, RequestRejected
from apps.common.db.tenant_context import set_gym_context
from apps.members.exceptions import (
    CheckInIneligible,
    MembershipOverlap,
    MembershipStateConflict,
)
from apps.members.payment_services import (
    PaymentPostingConflict,
    PTPaymentOwned,
    RefundExceedsBalance,
    RetailCorrectionUnavailable,
)
from apps.tenants.models import Gym, GymMembership
from apps.trainers.exceptions import AssignmentNotEffective, PackageNotEligible
from apps.users.permissions import IsActiveOwner, RequiresEntitlements

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are the GymPilot assistant for the signed-in gym and staff member. "
    "Understand what the person is trying to do from the conversation, the "
    "verified references, and the tool results. People phrase the same request "
    "differently, change their minds, ask side questions, and return to earlier "
    "work. Follow the meaning. "
    "You choose which tools to call and in what order. The server runs only the "
    "tools you call, in this gym, and does not add a read you skipped. The live "
    "gym brief is a partial snapshot. Call a read when the question needs a "
    "complete, filtered, or current record. If a result does not contain the "
    "fact, say that you cannot retrieve it. Do not invent the record. Do not "
    "call a write tool to answer a question. Do not invent an amount, person, "
    "payment, sale, expense, "
    "attendance, booking, class time, capacity, stock quantity, salary, "
    "commission, or date. "
    "A write only proposes a change. Never say that change is saved before the "
    "server confirms it. Chat text never confirms a waiting change. The staff "
    "uses the confirmation control. If a confirmation is already waiting, do "
    "not propose another write. Call cancel_pending_confirmation when the staff "
    "member wants that prepared change dropped. That does not delete a saved "
    "record. "
    "Never ask for or send a gym id. Never send confirmed. Never repeat a tool "
    "name, exception, database error, or stack trace. Never tell the staff "
    "which screen to open. "
    "Read tools look up records. Send the name or number the staff used. If a "
    "tool returns ambiguous, list the choices and ask which one. Do not choose. "
    "A tool error is a fact: ask for what is missing or continue from that "
    "result. Do not invent the missing fact. For an approval or rejection, send "
    "the record's own id. The server supplies the stored amount. The record "
    "must already be submitted and waiting. If the tool says not_pending, "
    "submit that record first. "
    "Verified references are ids and values the server stored from tool results "
    "and confirmed writes. Use them instead of guessing an id from a name. A "
    "person may still correct a fact or mean a different record. "
    "There is no daily shift roster. Passwords, sessions, and audit storage are "
    "not available. Keep stored names, numbers, codes, dates, amounts, and "
    "statuses exactly as stored. Myanmar digits have the same values as Arabic "
    "digits. Use the gym timezone for relative days. Reply in the staff "
    "member's language."
)
_ANSWER_NOW = (
    "Answer now from the tool results already in this conversation. Do not call more tools."
)
_CONTINUE = (
    "The previous change is saved. Do not propose that same change again. "
    "Continue only the work this conversation still needs."
)
_UNAVAILABLE = "The assistant is unavailable."
_LIMIT = "I could not finish that within the assistant limit."
_BLOCKED = "I can't help with that request."
_CANCEL = "cancel_pending_confirmation"


class AssistantUnavailable(CodedAPIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "ASSISTANT_UNAVAILABLE"
    default_detail = "The assistant is unavailable."


_HISTORY = 24
_READ_LIMIT = 12000
_GOAL_TTL = timedelta(hours=24)


def run_turn(
    *,
    request: Request,
    view: APIView,
    message: str,
    conversation_id: UUID | None,
) -> dict[str, object]:
    """Persist the staff message, call Gemini, and execute reads only."""

    if not assistant_configuration().available:
        raise AssistantUnavailable()
    gym, actor = _session(request)
    require_gym_ai(gym)
    conversation = _conversation(gym, actor, conversation_id, message)
    append_message(
        gym=gym,
        conversation=conversation,
        actor=actor,
        role=Message.Role.STAFF,
        body=message,
    )
    expired = _drop_expired(gym, conversation, actor)
    answered = _reply(
        request=request,
        view=view,
        gym=gym,
        actor=actor,
        conversation=conversation,
        instruction=_SYSTEM,
        cue=("The previous confirmation expired and nothing was saved." if expired else ""),
        provider_fallback=_UNAVAILABLE,
        staff_message=message,
    )
    if answered is None:
        raise AssistantUnavailable()
    return answered


def _continue_task(
    *,
    request: Request,
    view: APIView,
    gym: Gym,
    actor: GymMembership,
    conversation: Conversation,
    saved: tuple[str, Mapping[str, object]],
) -> dict[str, object] | None:
    """Ask the next fact or propose the next change after a saved step."""

    return _reply(
        request=request,
        view=view,
        gym=gym,
        actor=actor,
        conversation=conversation,
        instruction=_SYSTEM + " " + _CONTINUE,
        cue="The change is saved.",
        provider_fallback=None,
        saved=saved,
    )


def _reply(
    *,
    request: Request,
    view: APIView,
    gym: Gym,
    actor: GymMembership,
    conversation: Conversation,
    instruction: str,
    cue: str,
    provider_fallback: str | None,
    saved: tuple[str, Mapping[str, object]] | None = None,
    staff_message: str = "",
) -> dict[str, object] | None:
    pending = _waiting(gym, conversation, actor)
    history = _history(gym, conversation)
    offered = _offered(request)
    contracts = tool_contracts()
    staff_text = staff_message.strip() or _staff_text(history)
    declarations = _model_declarations(offered, contracts, staff_text)
    declarations.append(_cancel_declaration())
    contents = _contents(history)
    if cue:
        instruction += "\n" + cue
        contents.append({"role": "user", "parts": [{"text": cue}]})
    activity: list[dict[str, str]] = []
    proposal = pending
    deadline = monotonic() + max_execution_seconds()
    calls_used = 0
    model_calls = 0
    reply = ""
    read_completed = False
    read_results: list[Mapping[str, object]] = []
    opened_now = False
    limit_reached = False
    provider_down = False
    if pending is not None:
        instruction += " A confirmation is already waiting. Do not propose another write."
    workflow = load_state(gym, conversation)
    remember_sale_package(gym, conversation, workflow)
    context = assistant_context(gym, _codes(request), assistant_full_access(request))
    opening_card = (
        "Verified references come from tool results and confirmed writes. "
        "Conversation history is what was said. A waiting confirmation is not saved."
    )
    instruction += "\n\n" + opening_card
    instruction += "\n" + state_brief(workflow)
    instruction += "\n\n" + context
    marker = "Live gym brief"
    live_brief = context[context.find(marker) :] if marker in context else ""
    seen_errors: set[tuple[str, str]] = set()
    try:
        for _step in range(max_steps()):
            opened_now = False
            if monotonic() >= deadline:
                reply = reply or _LIMIT
                break
            model_calls += 1
            turn = generate_content(
                contents=contents,
                declarations=declarations,
                system_instruction=instruction,
            )
            if turn.blocked:
                reply = _BLOCKED
                break
            if not turn.calls:
                reply = turn.text or _LIMIT
                break
            contents.append({"role": "model", "parts": [dict(part) for part in turn.parts]})
            responses: list[dict[str, object]] = []
            repeated_error = False
            for call in turn.calls:
                if calls_used >= max_tool_calls():
                    responses.append(_function_response(call.name, {"error": "step_limit"}))
                    activity.append({"name": call.name, "status": "limit"})
                    continue
                calls_used += 1
                outcome, body = _handle_call(
                    request=request,
                    view=view,
                    gym=gym,
                    actor=actor,
                    conversation=conversation,
                    name=call.name,
                    arguments=call.arguments,
                    offered=offered,
                    proposal=proposal,
                    saved=saved,
                    staff_text=staff_text,
                    workflow=workflow,
                )
                if body.get("status") in {"cancelled", "expired"}:
                    proposal = None
                elif outcome is not None:
                    proposal = outcome
                    opened_now = True
                error_code = body.get("error")
                if isinstance(error_code, str) and error_code:
                    key = (call.name, error_code)
                    if key in seen_errors:
                        repeated_error = True
                    seen_errors.add(key)
                note_verified(workflow, call.name, body)
                responses.append(_function_response(call.name, body))
                if "result" in body:
                    read_completed = True
                    result = body.get("result")
                    if isinstance(result, Mapping):
                        read_results.append(result)
                if body.get("error"):
                    field = body.get("field")
                    safe_field = (
                        field
                        if isinstance(field, str)
                        and field.isascii()
                        and field.replace("_", "").isalnum()
                        and len(field) <= 40
                        else ""
                    )
                    logger.info(
                        "assistant tool failed: %s field=%s",
                        body.get("error"),
                        safe_field or "none",
                    )
                activity.append({"name": call.name, "status": _activity_status(body)})
            contents.append({"role": "user", "parts": responses})
            if opened_now:
                reply = turn.text or _proposal_text(proposal)
                break
            if repeated_error:
                reply = turn.text or reply or _LIMIT
                break
        else:
            limit_reached = True
            reply, extra_calls = _answer_from_results(
                contents=contents,
                instruction=instruction,
                results=read_results,
                brief=live_brief,
                staff_text=staff_text,
                deadline=deadline,
            )
            model_calls += extra_calls
    except ProviderCallError as exc:
        provider_down = True
        logger.info("assistant provider failed: %s", exc.failure.value)
        if provider_fallback is None:
            _log_turn(
                model_calls=model_calls,
                tool_calls=calls_used,
                activity=activity,
                declarations=declarations,
                contracts=contracts,
                limit_reached=limit_reached,
                workflow=workflow,
                opening_card=opening_card,
            )
            save_state(gym, conversation, workflow)
            return None
        reply = (
            provider_notice(staff_text, workflow)
            if provider_fallback == _UNAVAILABLE
            else provider_fallback
        )
        activity.append({"name": "assistant", "status": "unavailable"})
    _log_turn(
        model_calls=model_calls,
        tool_calls=calls_used,
        activity=activity,
        declarations=declarations,
        contracts=contracts,
        limit_reached=limit_reached,
        workflow=workflow,
        opening_card=opening_card,
    )
    save_state(gym, conversation, workflow)
    if reply in {"", _LIMIT}:
        summary = proposal["summary"] if proposal is not None else _LIMIT
        reply = summary if isinstance(summary, str) else _LIMIT
    if not provider_down and reply not in {_LIMIT, _BLOCKED}:
        ground = _grounding_text(history, workflow, live_brief)
        if read_completed:
            corrected = grounded_read_reply(
                reply,
                results=read_results,
                brief=ground,
                staff_text=staff_text,
                fallback=True,
            )
        else:
            corrected = grounded_information_reply(
                reply,
                brief=ground,
                staff_text=staff_text,
            )
        if corrected:
            reply = corrected
    if not provider_down:
        reply = visible_reply(
            reply,
            confirmed=saved is not None,
            proposed=proposal is not None,
            staff_text=staff_text,
        )
    reply = confirmation_reply(reply, proposal, staff_text, workflow)
    recorded = append_message(
        gym=gym,
        conversation=conversation,
        actor=None,
        role=Message.Role.ASSISTANT,
        body=reply[:4000],
        goal=_goal_or_none(gym, proposal),
    )
    return {
        "conversation_id": conversation.id,
        "message": message_data(recorded),
        "pending_confirmation": proposal,
        "tool_activity": [public_activity(item["name"], item["status"]) for item in activity],
    }


def confirm_step(*, request: Request, view: APIView, step_id: UUID) -> dict[str, object]:
    """Revalidate the stored proposal and run the existing write executor."""

    gym, actor = _session(request)
    require_gym_ai(gym)
    goal, step = _owned_step(gym, actor, step_id)
    if step.status == WorkflowStep.Status.SUCCEEDED:
        return _confirmation_result(gym, goal, step, "existing", "That change is already complete.")
    if step.status != WorkflowStep.Status.WAITING_CONFIRMATION:
        raise RequestRejected({"confirmation": ["This confirmation is no longer waiting."]})
    if goal.expires_at <= timezone.now():
        close_goal(
            gym=gym,
            goal=goal,
            step=step,
            status=Goal.Status.EXPIRED,
            step_status=WorkflowStep.Status.CANCELLED,
        )
        raise RequestRejected({"confirmation": ["This confirmation has expired."]})
    payload = _confirmed_payload(step)
    if step.tool_name == "create_pt_sale" and sale_package_failure(gym, payload) is not None:
        _cancel_operation(gym, goal.conversation, "cancelled")
        close_goal(
            gym=gym,
            goal=goal,
            step=step,
            status=Goal.Status.CANCELLED,
            step_status=WorkflowStep.Status.CANCELLED,
            summary="package_unavailable",
        )
        raise RequestRejected(
            {
                "confirmation": [
                    gate_sentence(
                        "package_unavailable",
                        _latest_staff(gym, goal.conversation),
                    )
                ]
            }
        )
    try:
        result = execute_confirmed_write(
            request=request,
            view=view,
            tool_name=step.tool_name,
            goal=goal,
            step_id=step.id,
            expected_version=goal.version,
            payload=payload,
            confirmed=True,
        )
    except ExecutionRejected as exc:
        logger.info("assistant confirmation rejected: %s", exc.code)
        raise RequestRejected(
            {"confirmation": [_confirmation_text(gym, goal.conversation, "rejected")]}
        ) from exc
    except PermissionDenied:
        raise
    except Http404:
        logger.info("assistant confirmation missing record")
        raise RequestRejected(
            {"confirmation": [_confirmation_text(gym, goal.conversation, "missing_record")]}
        ) from None
    except ValidationError as exc:
        logger.info("assistant confirmation invalid")
        raise RequestRejected(
            {"confirmation": [_confirmation_text(gym, goal.conversation, _validation_kind(exc))]}
        ) from exc
    except MembershipOverlap:
        logger.info("assistant confirmation membership overlap")
        raise RequestRejected(
            {
                "confirmation": [
                    _confirmation_text(
                        gym,
                        goal.conversation,
                        "membership_overlap",
                        membership_conflict(gym, payload),
                    )
                ]
            }
        ) from None
    except MembershipStateConflict as exc:
        logger.info("assistant confirmation membership state")
        kind = "plan_inactive" if "plan" in str(exc.detail).casefold() else "member_inactive"
        raise RequestRejected(
            {"confirmation": [_confirmation_text(gym, goal.conversation, kind)]}
        ) from exc
    except ClassFull:
        logger.info("assistant confirmation class full")
        raise RequestRejected(
            {"confirmation": [_confirmation_text(gym, goal.conversation, "class_full")]}
        ) from None
    except ScheduleConflict:
        logger.info("assistant confirmation schedule conflict")
        kind = (
            "duplicate_booking" if member_has_class_booking(gym, payload) else "schedule_conflict"
        )
        raise RequestRejected(
            {"confirmation": [_confirmation_text(gym, goal.conversation, kind)]}
        ) from None
    except RefundExceedsBalance:
        logger.info("assistant confirmation refund exceeds balance")
        raise RequestRejected(
            {
                "confirmation": [
                    _confirmation_text(
                        gym,
                        goal.conversation,
                        "refund_exceeds",
                        {"balance": _balance_text(gym, payload)},
                    )
                ]
            }
        ) from None
    except (PaymentPostingConflict, PTPaymentOwned):
        logger.info("assistant confirmation payment state")
        raise RequestRejected(
            {"confirmation": [_confirmation_text(gym, goal.conversation, "payment_state")]}
        ) from None
    except RetailCorrectionUnavailable:
        logger.info("assistant confirmation retail charge")
        raise RequestRejected(
            {"confirmation": [_confirmation_text(gym, goal.conversation, "retail_charge")]}
        ) from None
    except AssignmentNotEffective:
        close_goal(
            gym=gym,
            goal=goal,
            step=step,
            status=Goal.Status.CANCELLED,
            step_status=WorkflowStep.Status.CANCELLED,
            summary="assignment_required",
        )
        followed = _reply(
            request=request,
            view=view,
            gym=gym,
            actor=actor,
            conversation=goal.conversation,
            instruction=_SYSTEM,
            cue=(
                "The sale or booking was not saved. That trainer is not assigned "
                "to the member on the service date."
            ),
            provider_fallback=_confirmation_text(gym, goal.conversation, "assignment"),
            saved=None,
        )
        if followed is not None:
            return followed
        return _confirmation_result(
            gym,
            goal,
            step,
            "cancelled",
            _confirmation_text(gym, goal.conversation, "assignment"),
        )
    except PackageNotEligible:
        if step.tool_name == "create_pt_sale":
            _cancel_operation(gym, goal.conversation, "cancelled")
            close_goal(
                gym=gym,
                goal=goal,
                step=step,
                status=Goal.Status.CANCELLED,
                step_status=WorkflowStep.Status.CANCELLED,
                summary="package_unavailable",
            )
            raise RequestRejected(
                {
                    "confirmation": [
                        gate_sentence(
                            "package_unavailable",
                            _latest_staff(gym, goal.conversation),
                        )
                    ]
                }
            ) from None
        class_booking = step.tool_name == "create_class_booking"
        close_goal(
            gym=gym,
            goal=goal,
            step=step,
            status=Goal.Status.CANCELLED,
            step_status=WorkflowStep.Status.CANCELLED,
            summary="package_required",
        )
        followed = _reply(
            request=request,
            view=view,
            gym=gym,
            actor=actor,
            conversation=goal.conversation,
            instruction=_SYSTEM,
            cue=(
                "The booking was not saved. The member has no active class package "
                "with sessions left for that class."
                if class_booking
                else "The booking was not saved. The member has no active PT package "
                "with sessions left on that date."
            ),
            provider_fallback=_confirmation_text(
                gym,
                goal.conversation,
                "class_package" if class_booking else "package",
            ),
            saved=None,
        )
        if followed is not None:
            return followed
        return _confirmation_result(
            gym,
            goal,
            step,
            "cancelled",
            _confirmation_text(
                gym,
                goal.conversation,
                "class_package" if class_booking else "package",
            ),
        )
    except CheckInIneligible:
        logger.info("assistant confirmation check-in ineligible")
        close_goal(
            gym=gym,
            goal=goal,
            step=step,
            status=Goal.Status.CANCELLED,
            step_status=WorkflowStep.Status.CANCELLED,
            summary="check_in_ineligible",
        )
        return _confirmation_result(
            gym,
            goal,
            step,
            "cancelled",
            _confirmation_text(gym, goal.conversation, "check_in_ineligible"),
        )
    if result.outcome in {"committed", "existing"}:
        workflow = load_state(gym, goal.conversation)
        remember_commit(gym, workflow, step.tool_name, payload, result.effect_reference)
        save_state(gym, goal.conversation, workflow)
        close_goal(
            gym=gym,
            goal=goal,
            step=step,
            status=Goal.Status.COMPLETED,
            step_status=WorkflowStep.Status.SUCCEEDED,
            summary=step.tool_name,
        )
        followed = _continue_task(
            request=request,
            view=view,
            gym=gym,
            actor=actor,
            conversation=goal.conversation,
            saved=(step.tool_name, payload),
        )
        if followed is not None:
            followed["outcome"] = result.outcome
            return followed
        staff_text = _staff_text(_history(gym, goal.conversation))
        return _confirmation_result(
            gym,
            goal,
            step,
            result.outcome,
            completion_sentence(workflow, staff_text),
        )
    if result.outcome == "in_progress":
        text = "That change is already in progress."
    elif result.outcome == "bookkeeping_pending":
        text = "That change is recorded. Confirm it again to finish the assistant record."
    else:
        text = "The conversation changed. Confirm this step again."
    return _confirmation_result(gym, goal, step, result.outcome, text)


def reject_step(*, request: Request, view: APIView, step_id: UUID) -> dict[str, object]:
    """Cancel a waiting confirmation without calling an executor."""

    del view
    gym, actor = _session(request)
    require_gym_ai(gym)
    goal, step = _owned_step(gym, actor, step_id)
    if step.status == WorkflowStep.Status.CANCELLED:
        return _confirmation_result(gym, goal, step, "cancelled", "The change was rejected.")
    if step.status != WorkflowStep.Status.WAITING_CONFIRMATION:
        raise RequestRejected({"confirmation": ["This confirmation is no longer waiting."]})
    close_goal(
        gym=gym,
        goal=goal,
        step=step,
        status=Goal.Status.CANCELLED,
        step_status=WorkflowStep.Status.CANCELLED,
        summary="rejected",
    )
    return _confirmation_result(gym, goal, step, "cancelled", "The change was rejected.")


def _same_saved_change(
    name: str,
    payload: Mapping[str, object],
    saved: tuple[str, Mapping[str, object]] | None,
) -> bool:
    """A just-saved write must not be proposed again on the next turn."""

    if saved is None or name != saved[0]:
        return False
    return _plain_change(payload) == _plain_change(saved[1])


def _plain_change(payload: Mapping[str, object]) -> dict[str, str]:
    words = {
        "day": "day",
        "days": "day",
        "week": "week",
        "weeks": "week",
        "month": "month",
        "months": "month",
        "year": "year",
        "years": "year",
    }
    plain: dict[str, str] = {}
    for key, value in payload.items():
        if key.casefold() in {"confirmed", "gym_id"}:
            continue
        text = str(value).strip().casefold()
        if key.casefold() == "duration_unit":
            text = words.get(text, text)
        plain[key.casefold()] = text
    return plain


def waiting_confirmation(
    *,
    gym: Gym,
    conversation: Conversation,
    actor: GymMembership,
) -> dict[str, object] | None:
    return _waiting(gym, conversation, actor)


def _handle_call(
    *,
    request: Request,
    view: APIView,
    gym: Gym,
    actor: GymMembership,
    conversation: Conversation,
    name: str,
    arguments: Mapping[str, object],
    offered: frozenset[str],
    proposal: dict[str, object] | None,
    saved: tuple[str, Mapping[str, object]] | None = None,
    staff_text: str = "",
    workflow: dict[str, str] | None = None,
) -> tuple[dict[str, object] | None, dict[str, object]]:
    if name == _CANCEL:
        return None, _cancel_pending(gym, conversation, actor)
    if name not in offered:
        return None, {"error": "unknown_tool"}
    contract = tool_contracts().get(name)
    if contract is None or contract.definition.external_side_effects:
        return None, {"error": "unknown_tool"}
    tool = contract.definition
    try:
        payload = model_arguments(arguments, approval=tool.approval_required)
    except ValueError:
        return None, {"error": "invalid_arguments"}
    if tool.operation == "read":
        body = _read(request, view, name, payload)
        result = body.get("result")
        if workflow is not None and isinstance(result, Mapping) and "error" not in body:
            absorb_read(workflow, name, result)
        return None, body
    if tool.operation != "write":
        return None, {"error": "unknown_tool"}
    if proposal is not None:
        return None, {
            "status": "waiting",
            "saved": False,
            "detail": (
                "A confirmation is already waiting. This message did not "
                "confirm it, and nothing was saved."
            ),
        }
    if tool.approval_required:
        prepared, failure = _prepare_approval(gym, tool, payload)
        if failure is not None:
            return None, failure
        payload = prepared
    try:
        payload, labels, failure = resolve_references(
            gym,
            conversation,
            payload,
            permission_codes=_codes(request),
            stored_date=(workflow or {}).get("start_date", ""),
            require_join="member" in (workflow or {}).get("goals", ""),
            staff_text=staff_text,
        )
        if failure is not None:
            return None, failure
        if name == "create_pt_sale" and workflow is not None:
            extra, package_failure = attach_sale_package(gym, conversation, payload, workflow)
            if package_failure is not None:
                return None, package_failure
            labels.update(extra)
        meter = unmetered_gate(gym, actor, name, payload, staff_text)
        if meter is not None:
            return None, meter
        gap = assignment_gap(gym, name, payload)
        if gap is not None:
            return None, gap
        duplicate = existing_assignment(gym, name, payload)
        if duplicate is not None:
            return None, duplicate
        package = booking_package_gap(gym, name, payload)
        if package is not None:
            return None, package
        blocked = proposal_block(gym, actor, name, payload, staff_text)
        if blocked is not None:
            return None, blocked
        if _same_saved_change(name, payload, saved):
            return None, {"status": "already_saved"}
        if contract.prerequisite is not None:
            missing = missing_fields(contract.prerequisite, payload)
            if missing:
                return None, {"error": "missing_fields", "fields": list(missing)}
        opened = _open_proposal(
            gym=gym,
            actor=actor,
            conversation=conversation,
            tool=tool,
            payload=payload,
            labels=labels,
        )
        if workflow is not None and opened[0] is not None:
            absorb_proposal(workflow, name, payload)
        return opened
    except PermissionDenied:
        return None, {"error": "permission_denied"}
    except Http404:
        return None, {"error": "not_found"}
    except ValidationError:
        return None, {"error": "invalid_arguments"}
    except Exception:
        logger.exception("assistant tool failed")
        return None, {"error": "unavailable"}


def _open_proposal(
    *,
    gym: Gym,
    actor: GymMembership,
    conversation: Conversation,
    tool: ToolDefinition,
    payload: dict[str, object],
    labels: dict[str, str],
) -> tuple[dict[str, object] | None, dict[str, object]]:
    approval = _approval_pair(payload) if tool.approval_required else None
    summary = summary_for(tool, payload, approval)
    required: dict[str, object] = {
        "actor_membership_id": str(actor.id),
        "payload": payload,
        "approval": approval,
        "summary": summary,
        "labels": labels,
    }
    opened = open_confirmation(
        gym=gym,
        actor=actor,
        conversation=conversation,
        intent=tool.description,
        tool_name=tool.name,
        required_input=required,
        expires_at=timezone.now() + _GOAL_TTL,
    )
    if opened is None:
        return None, {
            "status": "waiting",
            "saved": False,
            "detail": (
                "A confirmation is already waiting. This message did not "
                "confirm it, and nothing was saved."
            ),
        }
    goal, step = opened
    card = public_pending(required, step_id=step.id, goal_id=goal.id, tool_name=tool.name)
    response: dict[str, object] = {"status": "confirmation_required", "summary": summary}
    if approval is not None:
        response["record_id"] = approval["record_id"]
        response["amount"] = approval["amount"]
    return card, response


def _prepare_approval(
    gym: Gym,
    tool: ToolDefinition,
    payload: dict[str, object],
) -> tuple[dict[str, object], dict[str, object] | None]:
    source = approval_source(tool.name)
    if source is None:
        return payload, {"error": "unknown_tool"}
    source_name, loader = source
    source_id = payload.get(source_name)
    if not isinstance(source_id, str) or source_id == "":
        return payload, {"error": "missing_fields", "fields": [source_name]}
    try:
        snapshot = loader(gym, source_id)
    except ExecutionRejected:
        return payload, {"error": "not_found"}
    if snapshot.status not in {"", "PENDING_APPROVAL"}:
        return payload, {"error": "not_pending", "status": snapshot.status}
    return with_snapshot(payload, snapshot), None


def _read(
    request: Request,
    view: APIView,
    name: str,
    payload: Mapping[str, object],
) -> dict[str, object]:
    try:
        result = execute_offered_read(request=request, view=view, tool_name=name, payload=payload)
    except ExecutionRejected as exc:
        logger.info("assistant read rejected: %s", exc.code)
        return {"error": _safe_error_code(exc.code)}
    except PermissionDenied:
        return {"error": "permission_denied"}
    except Http404:
        return {"error": "not_found"}
    except ValidationError:
        return {"error": "invalid_arguments"}
    except Exception:
        logger.exception("assistant read failed")
        return {"error": "unavailable"}
    return {"result": _public_read(result)}


def _public_read(result: Mapping[str, object]) -> dict[str, object]:
    try:
        encoded = json.dumps(result, default=str)
    except (TypeError, ValueError):
        return {"status": "completed"}
    if len(encoded) > _READ_LIMIT:
        rows = result.get("results")
        count = len(rows) if isinstance(rows, list) else None
        if count is None:
            return {"status": "completed"}
        return {"count": count, "status": "completed"}
    parsed = json.loads(encoded)
    if isinstance(parsed, dict):
        return cast(dict[str, object], parsed)
    return {"status": "completed"}


def _confirmation_result(
    gym: Gym,
    goal: Goal,
    step: WorkflowStep,
    outcome: str,
    text: str,
) -> dict[str, object]:
    saved = append_message(
        gym=gym,
        conversation=goal.conversation,
        actor=None,
        role=Message.Role.ASSISTANT,
        body=text,
        goal=goal,
    )
    return {
        "conversation_id": goal.conversation_id,
        "message": message_data(saved),
        "pending_confirmation": None
        if outcome in {"committed", "existing", "cancelled"}
        else public_pending(
            step.required_input,
            step_id=step.id,
            goal_id=goal.id,
            tool_name=step.tool_name,
        ),
        "tool_activity": [public_activity(step.tool_name, outcome)],
        "outcome": outcome,
    }


def _confirmed_payload(step: WorkflowStep) -> dict[str, object]:
    payload = {
        key: value
        for key, value in stored_payload(step).items()
        if key.casefold() not in {"confirmed", "gym_id"}
    }
    if not payload:
        raise RequestRejected({"confirmation": ["This confirmation has no stored change."]})
    return payload


def _owned_step(gym: Gym, actor: GymMembership, step_id: UUID) -> tuple[Goal, WorkflowStep]:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        step = (
            WorkflowStep.objects.select_related("goal", "goal__conversation")
            .filter(gym=gym, id=step_id)
            .first()
        )
        if step is None:
            raise Http404
        goal = step.goal
        raw = step.required_input if isinstance(step.required_input, dict) else {}
        owner = raw.get("actor_membership_id")
        if (
            goal.created_by_membership_id != actor.id
            or goal.conversation.created_by_membership_id != actor.id
            or owner != str(actor.id)
        ):
            raise Http404
        return goal, step


def _waiting(
    gym: Gym,
    conversation: Conversation,
    actor: GymMembership,
) -> dict[str, object] | None:
    return _pending_card(_waiting_step(gym, conversation, actor))


def _waiting_step(
    gym: Gym,
    conversation: Conversation,
    actor: GymMembership,
) -> tuple[Goal, WorkflowStep] | None:
    if conversation.created_by_membership_id != actor.id:
        return None
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        goal = (
            Goal.objects.filter(
                gym=gym,
                conversation=conversation,
                is_active=True,
                status=Goal.Status.WAITING_CONFIRMATION,
            )
            .order_by("created_at")
            .first()
        )
        if goal is None or goal.created_by_membership_id != actor.id:
            return None
        step = (
            WorkflowStep.objects.filter(
                gym=gym,
                goal=goal,
                status=WorkflowStep.Status.WAITING_CONFIRMATION,
            )
            .order_by("position")
            .first()
        )
    if step is None:
        return None
    raw = step.required_input if isinstance(step.required_input, dict) else {}
    if raw.get("actor_membership_id") != str(actor.id):
        return None
    return goal, step


def _pending_card(pair: tuple[Goal, WorkflowStep] | None) -> dict[str, object] | None:
    if pair is None:
        return None
    goal, step = pair
    raw = step.required_input if isinstance(step.required_input, dict) else {}
    return public_pending(raw, step_id=step.id, goal_id=goal.id, tool_name=step.tool_name)


def _drop_expired(gym: Gym, conversation: Conversation, actor: GymMembership) -> bool:
    """Close a confirmation whose time has passed. The message text is not read."""

    pair = _waiting_step(gym, conversation, actor)
    if pair is None:
        return False
    goal, step = pair
    if goal.expires_at > timezone.now():
        return False
    _cancel_operation(gym, conversation, "expired")
    close_goal(
        gym=gym,
        goal=goal,
        step=step,
        status=Goal.Status.EXPIRED,
        step_status=WorkflowStep.Status.CANCELLED,
        summary="expired",
    )
    return True


def _cancel_declaration() -> dict[str, object]:
    return {
        "name": _CANCEL,
        "description": (
            "Drop the confirmation waiting in this conversation. Call this when "
            "the staff member does not want that prepared change. This does not "
            "confirm a change and does not delete a saved record. If nothing is "
            "waiting, the result says so."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    }


def _cancel_pending(
    gym: Gym,
    conversation: Conversation,
    actor: GymMembership,
) -> dict[str, object]:
    pair = _waiting_step(gym, conversation, actor)
    if pair is None:
        return {"status": "nothing_waiting"}
    goal, step = pair
    if goal.expires_at <= timezone.now():
        _cancel_operation(gym, conversation, "expired")
        close_goal(
            gym=gym,
            goal=goal,
            step=step,
            status=Goal.Status.EXPIRED,
            step_status=WorkflowStep.Status.CANCELLED,
            summary="expired",
        )
        return {"status": "expired"}
    _cancel_operation(gym, conversation, "cancelled")
    close_goal(
        gym=gym,
        goal=goal,
        step=step,
        status=Goal.Status.CANCELLED,
        step_status=WorkflowStep.Status.CANCELLED,
        summary="rejected",
    )
    return {"status": "cancelled"}


def _cancel_operation(gym: Gym, conversation: Conversation, stage: str) -> None:
    state = load_state(gym, conversation)
    state["stage"] = stage
    state["pending_tool"] = ""
    state["last_outcome"] = stage
    save_state(gym, conversation, state)


def _latest_staff(gym: Gym, conversation: Conversation) -> str:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        row = (
            Message.objects.filter(gym=gym, conversation=conversation, role=Message.Role.STAFF)
            .order_by("-created_at", "-id")
            .only("body")
            .first()
        )
    return row.body if row is not None else ""


def _conversation(
    gym: Gym,
    actor: GymMembership,
    conversation_id: UUID | None,
    message: str,
) -> Conversation:
    if conversation_id is None:
        title = " ".join(message.split())
        return open_conversation(gym=gym, actor=actor, title=title)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        conversation = get_conversation(gym=gym, actor=actor, conversation_id=conversation_id)
    if conversation.created_by_membership_id != actor.id:
        raise Http404
    return conversation


def _history(gym: Gym, conversation: Conversation) -> list[Message]:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return list(list_messages(gym=gym, conversation=conversation))


def _goal_or_none(gym: Gym, proposal: dict[str, object] | None) -> Goal | None:
    if proposal is None:
        return None
    goal_id = proposal.get("goal_id")
    if not isinstance(goal_id, UUID):
        return None
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return Goal.objects.filter(gym=gym, id=goal_id).first()


def _offered(request: Request) -> frozenset[str]:
    registry = production_registry()
    codes = _codes(request)
    full_access = assistant_full_access(request)
    owner = IsActiveOwner().has_permission(request, APIView())
    known = tool_contracts()
    names: set[str] = set()
    for name in registry.names():
        tool = registry.get(name)
        contract = known.get(name)
        if tool.external_side_effects or contract is None:
            continue
        if tool.requires_active_owner and not owner:
            continue
        if tool.features and not _entitled(request, tool.features):
            continue
        if tool.permissions:
            if not full_access and not _permission_granted(
                contract.permission_mode, tool.permissions, codes
            ):
                continue
        elif not tool.requires_active_owner:
            continue
        names.add(name)
    return frozenset(names)


def _permission_granted(mode: str, required: tuple[str, ...], codes: frozenset[str]) -> bool:
    """Use the same permission mode as execution.

    Class booking is offered for either ``classes.book`` or ``classes.manage``.
    Every other registered tool still requires each named permission.
    """

    if mode == "any":
        return any(code in codes for code in required)
    if mode == "all":
        return set(required).issubset(codes)
    return False


def _entitled(request: Request, features: tuple[str, ...]) -> bool:
    gym = getattr(request, "gym", None)
    if not isinstance(gym, Gym):
        return False

    class _Features:
        required_features = features

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return bool(RequiresEntitlements().has_permission(request, cast(APIView, _Features())))


def _codes(request: Request) -> frozenset[str]:
    granted: object = getattr(request, "permission_codes", frozenset[str]())
    if isinstance(granted, frozenset):
        return frozenset(code for code in granted if isinstance(code, str))
    if isinstance(granted, set):
        return frozenset(code for code in granted if isinstance(code, str))
    return frozenset()


def _session(request: Request) -> tuple[Gym, GymMembership]:
    gym = getattr(request, "gym", None)
    actor = getattr(request, "gym_membership", None)
    if not isinstance(gym, Gym) or not isinstance(actor, GymMembership) or actor.id is None:
        raise PermissionDenied()
    if actor.gym_id != gym.id:
        raise PermissionDenied()
    return gym, actor


def _log_turn(
    *,
    model_calls: int,
    tool_calls: int,
    activity: list[dict[str, str]],
    declarations: list[dict[str, object]],
    contracts: Mapping[str, ToolContract],
    limit_reached: bool,
    workflow: Mapping[str, str],
    opening_card: str,
) -> None:
    """Record turn shape only. Prompts, names, and amounts stay out of the log."""

    del opening_card

    write_declarations = 0
    read_declarations = 0
    for item in declarations:
        name = item.get("name")
        contract = contracts.get(name) if isinstance(name, str) else None
        if contract is None:
            continue
        if contract.definition.operation == "write":
            write_declarations += 1
        elif contract.definition.operation == "read":
            read_declarations += 1
    tools = ",".join(
        f"{item['name']}:{item.get('status', '')}" for item in activity if item.get("name")
    )
    capability = workflow.get("workflow") or "none"
    logger.info(
        "assistant turn model_calls=%s tool_calls=%s tools=%s write_declarations=%s "
        "read_declarations=%s limit_reached=%s capability=%s workflow=%s",
        model_calls,
        tool_calls,
        tools or "none",
        write_declarations,
        read_declarations,
        limit_reached,
        capability,
        workflow.get("workflow") or "none",
    )


def _model_declarations(
    offered: frozenset[str],
    contracts: Mapping[str, ToolContract],
    staff_text: str,
) -> list[dict[str, object]]:
    """Offer every tool this staff member may use.

    The message text does not hide a tool. Execution still checks permission,
    entitlement, and the gym on the request.
    """

    del staff_text
    selected: list[dict[str, object]] = []
    for name in sorted(offered):
        contract = contracts.get(name)
        if contract is None:
            continue
        selected.append(declaration(contract))
    return selected


def _answer_from_results(
    *,
    contents: list[dict[str, object]],
    instruction: str,
    results: list[Mapping[str, object]],
    brief: str,
    staff_text: str,
    deadline: float,
) -> tuple[str, int]:
    """Ask for a final answer after the tool budget, then use the stored rows."""

    calls = 0
    text = ""
    if results and monotonic() < deadline:
        contents.append({"role": "user", "parts": [{"text": _ANSWER_NOW}]})
        try:
            turn = generate_content(
                contents=contents,
                declarations=[],
                system_instruction=instruction,
            )
            calls = 1
            if not turn.blocked and not turn.calls:
                text = turn.text
        except ProviderCallError:
            logger.info("assistant synthesis failed")
    if text:
        return text, calls
    if results:
        rendered = grounded_read_reply(
            "",
            results=results,
            brief=brief,
            staff_text=staff_text,
        )
        if rendered:
            return rendered, calls
    return "", calls


def _conversation_today(gym: Gym) -> date:
    """Gym-local today. A test double without a timezone uses the active clock."""

    name = getattr(gym, "timezone", None)
    if not isinstance(name, str) or not name:
        return timezone.localdate()
    try:
        return timezone.now().astimezone(ZoneInfo(name)).date()
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.localdate()


def _staff_text(history: list[Message]) -> str:
    for message in reversed(history):
        if message.role == Message.Role.STAFF and message.body.strip():
            return message.body
    return ""


def _grounding_text(history: list[Message], workflow: Mapping[str, str], brief: str) -> str:
    """Facts the reply may repeat: this gym's brief, verified refs, and what staff said."""

    said = [
        message.body.strip()[:500]
        for message in history
        if message.role == Message.Role.STAFF and message.body.strip()
    ]
    return "\n".join((brief, state_brief(workflow), *said[-12:]))


def _safe_error_code(code: str) -> str:
    if code.isascii() and code.replace("_", "").isalnum() and code.islower() and len(code) <= 40:
        return code
    return "unavailable"


def _confirmation_text(
    gym: Gym,
    conversation: Conversation,
    kind: str,
    detail: Mapping[str, object] | None = None,
) -> str:
    staff_text = _staff_text(_history(gym, conversation))
    return gate_sentence(kind, staff_text, detail)


def _validation_kind(exc: ValidationError) -> str:
    blob = str(exc.detail).casefold()
    if "stock" in blob or "not enough" in blob:
        return "stock_short"
    if "business date" in blob or "future" in blob:
        return "date_invalid"
    if "currency" in blob:
        return "currency_mismatch"
    if "draft" in blob:
        return "payment_state"
    return "invalid"


def _balance_text(gym: Gym, payload: Mapping[str, object]) -> str:
    payment_id = payload.get("payment_id")
    if not isinstance(payment_id, str):
        return ""
    balance = refundable_balance(gym, payment_id)
    if balance is None:
        return ""
    return format(balance, "f")


def _contents(messages: list[Message]) -> list[dict[str, object]]:
    contents: list[dict[str, object]] = []
    for message in messages[-_HISTORY:]:
        if is_state_message(message):
            continue
        text = message.body.strip()[:2000]
        if text == "":
            continue
        role = "model" if message.role == Message.Role.ASSISTANT else "user"
        if contents and contents[-1]["role"] == role:
            previous = contents[-1]["parts"]
            if isinstance(previous, list) and previous and isinstance(previous[0], dict):
                previous[0]["text"] = str(previous[0].get("text", "")) + "\n" + text
            continue
        contents.append({"role": role, "parts": [{"text": text}]})
    while contents and contents[0]["role"] != "user":
        contents.pop(0)
    return contents


def _function_response(name: str, body: dict[str, object]) -> dict[str, object]:
    return {"functionResponse": {"name": name, "response": body}}


def _proposal_text(proposal: Mapping[str, object] | None) -> str:
    if proposal is None:
        return ""
    summary = proposal.get("summary")
    return summary if isinstance(summary, str) else ""


def _activity_status(body: Mapping[str, object]) -> str:
    if body.get("status") == "confirmation_required":
        return "proposed"
    if body.get("status") == "waiting":
        return "waiting"
    if "error" in body:
        return "error"
    return "completed"


def _approval_pair(payload: Mapping[str, object]) -> dict[str, str] | None:
    record_id = payload.get("record_id")
    amount = payload.get("amount")
    if isinstance(record_id, str) and isinstance(amount, str) and record_id and amount:
        return {"record_id": record_id, "amount": amount}
    return None
