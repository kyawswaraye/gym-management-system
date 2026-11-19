"""One authenticated assistant turn. Writes wait for a separate confirmation."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
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
from apps.assistant.inventory_tools import catalog_already_stored
from apps.assistant.knowledge import (
    DOMAIN_MAP,
    assistant_context,
    gate_sentence,
    grounded_information_reply,
    grounded_read_reply,
    localized_summary,
    public_activity,
    recovery_sentence,
    reply_needs_fresh_read,
    speaks_burmese,
    staff_notice,
    unconfirmed_notice,
    visible_reply,
)
from apps.assistant.lookups import (
    align_class_schedule,
    assignment_gap,
    attach_cash_session,
    attach_payout_reversals,
    attach_sale_package,
    bind_latest_ledger_entry,
    booking_package_gap,
    existing_assignment,
    finance_record,
    member_has_class_booking,
    membership_conflict,
    prepare_cash_reconcile,
    proposal_block,
    refundable_balance,
    reject_repeat_publish,
    reject_repeat_submit,
    reject_uncancellable,
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
    asks_stored_date,
    completion_sentence,
    confirmation_reply,
    is_state_message,
    load_state,
    note_saved_change,
    note_verified,
    provider_notice,
    remember_commit,
    return_fingerprint,
    save_state,
    state_brief,
)
from apps.assistant.workflows import (
    catalog_text,
    get_workflow,
    guide_text,
    modules_for,
    sections_for,
    workflow_ids,
    workflows_for_module,
)
from apps.classes.exceptions import ClassFull, ScheduleConflict
from apps.common.api.exceptions import CodedAPIException, RequestRejected
from apps.common.db.tenant_context import set_gym_context
from apps.common.finance.exceptions import FinanceStateConflict
from apps.members.exceptions import (
    CheckInDuplicate,
    CheckInIneligible,
    CheckInOpen,
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
    "gym brief is a partial snapshot. Call a read in this turn when the question "
    "asks for stored records, even if an earlier turn already read them. Do not "
    "answer a stored-data question from memory. If a result does not contain the "
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
    "statuses exactly as stored. A stored code may contain an underscore. "
    "Quote that code exactly. An underscore does not make it a tool name. "
    "Myanmar digits have the same values as Arabic "
    "digits. Use the gym timezone for relative days. Reply in the staff "
    "member's language. "
    "A short decline only cancels the waiting confirmation. Call "
    "cancel_pending_confirmation and do not propose that change again. "
    "The loaded tools cover only the active workflows. When the staff asks "
    "about an area those tools do not cover, call select_workflow with the "
    "current workflow and the workflow for that area (use reports for a "
    "lookup or question) before answering. Never say you cannot access "
    "something before doing that."
)
_ANSWER_NOW = (
    "Answer now from the tool results already in this conversation. Do not call more tools."
)
_REQUIRE_READ = (
    "That answer stated stored records without a read tool in this turn. "
    "Call the read tool for this question now, then answer from its result. "
    "Do not reuse an earlier turn and do not answer before the read returns."
)
_CONTINUE = (
    "The previous change is saved. Do not propose that same change again. "
    "Continue only the work this conversation still needs."
)
_UNAVAILABLE = "The assistant is unavailable."
_LIMIT = "I could not finish that within the assistant limit."
_BLOCKED = "I can't help with that request."
_CANCEL = "cancel_pending_confirmation"
_SELECT = "select_workflow"
_OPENING = (
    "Verified references come from tool results and confirmed writes. "
    "Conversation history is what was said. A waiting confirmation is not saved."
)


class AssistantUnavailable(CodedAPIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "ASSISTANT_UNAVAILABLE"
    default_detail = "The assistant is unavailable."


_HISTORY = 12
_READ_LIMIT = 6000
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

    workflow = load_state(gym, conversation)
    status = workflow.get("record_status") or ""
    cue = "The change is saved."
    if status and status != "POSTED":
        cue += f" Stored status is {status}. It is not posted."
    if saved[0] == "return_retail_sale":
        cue += " That return is already stored. Do not return those goods again."
    if saved[0] == "create_product_category":
        cue += (
            " The category is saved. Do not create that category again. "
            "Create the product next with the SKU, unit, sale price, and cost "
            "the staff stated."
        )
    if saved[0] == "create_product":
        cue += (
            " The product is saved. Do not create it again. If the staff asked "
            "for an opening count, propose adjust_stock with movement_type "
            "OPENING, that quantity, and a short reason."
        )
    if status == "DRAFT":
        cue += " Submit this draft. It is not posted until that submit is approved."
    if status == "PENDING_APPROVAL":
        cue += " Use the approve tool for this same record. Do not submit it again."
    return _reply(
        request=request,
        view=view,
        gym=gym,
        actor=actor,
        conversation=conversation,
        instruction=_SYSTEM + " " + _CONTINUE,
        cue=cue,
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
    activity: list[dict[str, str]] = []
    proposal = pending
    deadline = monotonic() + max_execution_seconds()
    calls_used = 0
    model_calls = 0
    reply = ""
    read_completed = False
    read_nudge = False
    read_results: list[Mapping[str, object]] = []
    opened_now = False
    limit_reached = False
    provider_down = False
    workflow = load_state(gym, conversation)
    remember_sale_package(gym, conversation, workflow)
    active = _active_workflows(workflow)
    context = assistant_context(
        gym,
        _codes(request),
        assistant_full_access(request),
        sections=sections_for(active),
    )
    marker = "Live gym brief"
    live_brief = context[context.find(marker) :] if marker in context else ""
    contents = _contents(history)
    dynamic = "\n".join(part for part in (cue, state_brief(workflow), live_brief) if part)
    _attach_context(contents, dynamic)
    declarations = _workflow_declarations(offered, contracts, active)
    system_instruction = _system_instruction(
        instruction,
        pending=proposal is not None,
        active=active,
    )
    seen_errors: set[tuple[str, str]] = set()
    last_error: Mapping[str, object] | None = None
    failures: list[tuple[str, dict[str, object], dict[str, object]]] = []
    try:
        steps_used = 0
        free_selects = 0
        while steps_used < max_steps():
            opened_now = False
            if monotonic() >= deadline:
                reply = reply or _LIMIT
                break
            active = _active_workflows(workflow)
            declarations = _workflow_declarations(offered, contracts, active)
            system_instruction = _system_instruction(
                instruction,
                pending=proposal is not None,
                active=active,
            )
            model_calls += 1
            turn = generate_content(
                contents=contents,
                declarations=declarations,
                system_instruction=system_instruction,
            )
            if turn.blocked:
                reply = _BLOCKED
                break
            if not turn.calls:
                reply = turn.text or _LIMIT
                if _needs_fresh_read(
                    reply=reply,
                    staff_text=staff_text,
                    read_completed=read_completed,
                    nudged=read_nudge,
                    declarations=declarations,
                    contracts=contracts,
                    allowed=_grounding_text(history, workflow, live_brief),
                ):
                    # One extra provider call. The next loop either reads or stops.
                    read_nudge = True
                    contents.append({"role": "model", "parts": [dict(part) for part in turn.parts]})
                    contents.append({"role": "user", "parts": [{"text": _REQUIRE_READ}]})
                    continue
                break
            contents.append({"role": "model", "parts": [dict(part) for part in turn.parts]})
            responses: list[dict[str, object]] = []
            repeated_error = False
            only_select = True
            cancelled_now = False
            declared = _declaration_names(declarations)
            for call in turn.calls:
                if call.name != _SELECT:
                    only_select = False
                if calls_used >= max_tool_calls():
                    responses.append(_function_response(call.name, {"error": "step_limit"}))
                    activity.append({"name": call.name, "status": "limit"})
                    continue
                calls_used += 1
                if call.name == _SELECT:
                    body = _select_workflows(workflow, call.arguments)
                    responses.append(_function_response(call.name, body))
                    activity.append({"name": call.name, "status": _activity_status(body)})
                    continue
                if cancelled_now and call.name != _CANCEL:
                    contract = contracts.get(call.name)
                    operation = "" if contract is None else contract.definition.operation
                    if operation == "write":
                        body = {
                            "status": "cancelled",
                            "saved": False,
                            "message": ("The waiting change was dropped. Do not propose it again."),
                        }
                        responses.append(_function_response(call.name, body))
                        activity.append({"name": call.name, "status": "cancelled"})
                        continue
                if call.name not in declared and call.name in offered:
                    contract = contracts.get(call.name)
                    module = "" if contract is None else contract.definition.module
                    if contract is not None and _auto_activate(workflow, module):
                        declared = _declaration_names(
                            _workflow_declarations(
                                offered,
                                contracts,
                                _active_workflows(workflow),
                            )
                        )
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
                if body.get("status") == "cancelled":
                    cancelled_now = True
                    proposal = None
                elif body.get("status") == "expired":
                    proposal = None
                elif outcome is not None:
                    proposal = outcome
                    opened_now = True
                error_code = body.get("error")
                if isinstance(error_code, str) and error_code:
                    last_error = body
                    failures.append((call.name, dict(body), dict(call.arguments)))
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
            if only_select and free_selects < 2:
                free_selects += 1
                continue
            steps_used += 1
        else:
            limit_reached = True
            reply, extra_calls = _answer_from_results(
                contents=contents,
                instruction=system_instruction,
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
                opening_card=_OPENING,
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
        opening_card=_OPENING,
    )
    save_state(gym, conversation, workflow)
    explained = False
    if not provider_down and proposal is None and saved is None and failures:
        reply, proposal, opened_now = _explain_failure(
            request=request,
            view=view,
            gym=gym,
            actor=actor,
            conversation=conversation,
            offered=offered,
            staff_text=staff_text,
            workflow=workflow,
            failures=failures,
        )
        explained = True
    if reply in {"", _LIMIT}:
        summary = proposal["summary"] if proposal is not None else None
        if isinstance(summary, str) and summary:
            reply = summary
        elif last_error is not None:
            reply = staff_notice(last_error, staff_text) or _LIMIT
        else:
            reply = _LIMIT
    if not explained and not provider_down and reply not in {_LIMIT, _BLOCKED}:
        ground = _grounding_text(history, workflow, live_brief)
        if read_completed:
            corrected = grounded_read_reply(
                reply,
                results=read_results,
                brief=ground,
                staff_text=staff_text,
            )
        else:
            corrected = grounded_information_reply(
                reply,
                brief=ground,
                staff_text=staff_text,
            )
        # Every factual sentence was unsupported. Show the stored rows, or the
        # not-found notice. The model's invented text stays off the reply.
        reply = (
            corrected
            or staff_notice({"error": "not_found"}, staff_text)
            or ("No stored record matches that.")
        )
    burmese = _conversation_burmese(history, staff_text, reply)
    language = "မြန်မာ" if burmese else staff_text
    if not explained and not provider_down:
        reply = visible_reply(
            reply,
            confirmed=saved is not None,
            proposed=proposal is not None,
            staff_text=language,
        )
    if not provider_down and opened_now and saved is not None:
        # The previous write is stored. The card that is open now is a different step.
        done = completion_sentence(workflow, language)
        if burmese:
            reply = f"{done} နောက်တစ်ချက်ကို အတည်ပြုပြီးမှ သိမ်းပါမယ်။".strip()
        else:
            reply = f"{done} The next change is not saved until you confirm.".strip()
    elif (
        not explained
        and not provider_down
        and proposal is None
        and saved is None
        and last_error is not None
    ):
        notice = staff_notice(last_error, language)
        if notice and notice not in reply:
            reply = f"{notice} {reply}".strip()
    elif not provider_down and proposal is not None and saved is None:
        notice = unconfirmed_notice(language)
        english_notice = "That change is not saved until you confirm."
        if burmese and english_notice in reply:
            reply = reply.replace(english_notice, notice)
        if notice not in reply:
            reply = f"{reply} {notice}".strip()
    if not provider_down and burmese and proposal is not None:
        proposal, reply = _localize_pending(proposal, reply)
    reply = confirmation_reply(reply, proposal, language, workflow)
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
        code = _safe_error_code(exc.code)
        detail: dict[str, object] = {"error": code}
        record_status = _finance_status(gym, step.tool_name, payload)
        if record_status:
            detail["status"] = record_status
            if code == "rejected":
                detail["error"] = "finance_state"
        return _recovered_confirmation(
            request=request,
            view=view,
            gym=gym,
            actor=actor,
            goal=goal,
            step=step,
            body=detail,
            follow=_safe_followup(step.tool_name, detail, payload),
        )
    except PermissionDenied:
        raise
    except Http404:
        logger.info("assistant confirmation missing record")
        raise RequestRejected(
            {"confirmation": [_confirmation_text(gym, goal.conversation, "missing_record")]}
        ) from None
    except ValidationError as exc:
        kind = _validation_kind(exc)
        logger.info("assistant confirmation invalid kind=%s", kind)
        if kind == "already_saved":
            return _closed_confirmation(gym, goal, step, kind)
        close_goal(
            gym=gym,
            goal=goal,
            step=step,
            status=Goal.Status.CANCELLED,
            step_status=WorkflowStep.Status.CANCELLED,
            summary=kind,
        )
        hint = _validation_hint(exc)
        sentence = _confirmation_text(gym, goal.conversation, kind)
        if hint:
            sentence = f"{sentence} {hint}"
        raise RequestRejected({"confirmation": [sentence]}) from exc
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
        assignment = _assignment_arguments(step.tool_name, payload)
        if assignment is not None:
            return _recovered_confirmation(
                request=request,
                view=view,
                gym=gym,
                actor=actor,
                goal=goal,
                step=step,
                body={"error": "assignment_required"},
                follow=("create_assignment", assignment),
            )
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
        return _closed_confirmation(gym, goal, step, "check_in_ineligible")
    except CheckInOpen:
        logger.info("assistant confirmation check-in already open")
        return _closed_confirmation(gym, goal, step, "check_in_open")
    except FinanceStateConflict:
        logger.info("assistant confirmation finance state")
        detail = {"error": "finance_state"}
        record_status = _finance_status(gym, step.tool_name, payload)
        if record_status:
            detail["status"] = record_status
        return _recovered_confirmation(
            request=request,
            view=view,
            gym=gym,
            actor=actor,
            goal=goal,
            step=step,
            body=detail,
            follow=_safe_followup(step.tool_name, detail, payload),
        )
    except CheckInDuplicate:
        logger.info("assistant confirmation check-in duplicate")
        return _closed_confirmation(gym, goal, step, "check_in_duplicate")
    if result.outcome in {"committed", "existing"}:
        workflow = load_state(gym, goal.conversation)
        remember_commit(gym, workflow, step.tool_name, payload, result.effect_reference)
        note_saved_change(workflow, step.tool_name, _plain_change(payload))
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
    return _plain_change(payload, name) == _plain_change(saved[1], name)


_REMEMBERED_TOOLS = frozenset({"create_product_category", "create_product"})


def _remembered_saved_change(
    name: str,
    payload: Mapping[str, object],
    workflow: Mapping[str, str] | None,
) -> bool:
    """A saved category or product is not proposed again later in the conversation."""

    if name not in _REMEMBERED_TOOLS or not workflow:
        return False
    raw = workflow.get("saved_changes") or ""
    if not raw:
        return False
    try:
        existing = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if not isinstance(existing, list):
        return False
    current = _plain_change(payload)
    for item in existing:
        if not isinstance(item, Mapping) or item.get("tool") != name:
            continue
        stored = item.get("payload")
        if isinstance(stored, Mapping) and _plain_change(stored) == current:
            return True
    return False


_NAME_ONLY = re.compile(r"^[^\W\d_]+(?:[ '.-]+[^\W\d_]+){0,3}$")
_NAME_BLOCK = frozenset(
    {"add", "register", "create", "join", "member", "plan", "today", "package", "date"}
)


def _name_only_member(text: str) -> bool:
    """A bare name is not a registration. A verb or a plan still is a request."""

    token = " ".join(text.strip().strip("။.!?၊").split())
    if token == "" or len(token) > 48:
        return False
    if any(word in _NAME_BLOCK for word in token.casefold().split()):
        return False
    if any(mark in token for mark in ("လုပ်", "ဝယ်", "စာရင်း", "အဖွဲ့", "ဒီနေ့", "မနက်ဖြန်")):
        return False
    return _NAME_ONLY.fullmatch(token) is not None


_PIECE_ALIASES = frozenset({"PCS", "PC", "PIECES"})


def _align_product_unit(
    gym: Gym,
    conversation: Conversation,
    payload: dict[str, object],
    staff_text: str,
) -> None:
    """Store the unit in capitals. pcs is PIECE when the staff said PIECE."""

    unit = payload.get("unit")
    if not isinstance(unit, str):
        return
    token = re.sub(r"[^A-Za-z0-9_]", "", unit).upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,15}", token):
        return
    if token in _PIECE_ALIASES and _staff_said_piece(gym, conversation, staff_text):
        payload["unit"] = "PIECE"
        return
    payload["unit"] = token


def _staff_said_piece(gym: Gym, conversation: Conversation, staff_text: str) -> bool:
    if re.search(r"\bpiece\b", staff_text, re.IGNORECASE):
        return True
    said = "\n".join(
        message.body
        for message in _history(gym, conversation)
        if message.role == Message.Role.STAFF
    )
    return re.search(r"\bpiece\b", said, re.IGNORECASE) is not None


def _plain_change(payload: Mapping[str, object], tool: str = "") -> dict[str, str]:
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
        number = _plain_number(text)
        if number is not None:
            text = number
        plain[key.casefold()] = text
    if tool == "create_member":
        first = plain.pop("first_name", "")
        last = plain.pop("last_name", "")
        plain["full_name"] = " ".join(f"{first} {last}".split())
    return plain


def _plain_number(text: str) -> str | None:
    """Collapse equivalent amounts such as 50000, 50000.00, and 50,000."""

    cleaned = text.replace(",", "").replace("၊", "").strip()
    if cleaned == "":
        return None
    try:
        number = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


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
            tool_name=name,
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
        stock = _stock_cost(name, payload)
        if stock is not None:
            return None, stock
        wages = _wage_lines(name, payload)
        if wages is not None:
            return None, wages
        if name == "adjust_cash_session" and payload.get("description") in {None, ""}:
            payload["description"] = ""
        align_class_schedule(name, payload)
        session_labels, session_error = attach_cash_session(gym, name, payload)
        if session_error is not None:
            return None, session_error
        labels.update(session_labels)
        reversal_error = attach_payout_reversals(gym, name, payload)
        if reversal_error is not None:
            return None, reversal_error
        ledger_error = bind_latest_ledger_entry(gym, name, payload)
        if ledger_error is not None:
            return None, ledger_error
        reconcile_error = prepare_cash_reconcile(gym, name, payload)
        if reconcile_error is not None:
            return None, reconcile_error
        cancel_state = reject_uncancellable(gym, name, payload)
        if cancel_state is not None:
            return None, cancel_state
        repeat = reject_repeat_submit(gym, name, payload)
        if repeat is not None:
            return None, repeat
        published = reject_repeat_publish(gym, name, payload)
        if published is not None:
            return None, published
        if (
            name == "return_retail_sale"
            and workflow is not None
            and workflow.get("return_key")
            and workflow.get("return_key") == return_fingerprint(payload)
        ):
            return None, {
                "status": "already_saved",
                "message": "That return is already stored. Do not return those goods again.",
            }
        if name == "create_member" and _name_only_member(staff_text):
            return None, {"error": "missing_fields", "fields": ["join_date"]}
        if name in {"create_product", "update_product"}:
            _align_product_unit(gym, conversation, payload, staff_text)
        if name in {"create_product", "create_product_category"}:
            stored_catalog = catalog_already_stored(gym, name, payload)
            if stored_catalog is not None:
                return None, stored_catalog
        if _same_saved_change(name, payload, saved) or _remembered_saved_change(
            name, payload, workflow
        ):
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


def _closed_confirmation(
    gym: Gym,
    goal: Goal,
    step: WorkflowStep,
    kind: str,
) -> dict[str, object]:
    close_goal(
        gym=gym,
        goal=goal,
        step=step,
        status=Goal.Status.CANCELLED,
        step_status=WorkflowStep.Status.CANCELLED,
        summary=kind,
    )
    return _confirmation_result(
        gym,
        goal,
        step,
        "cancelled",
        _confirmation_text(gym, goal.conversation, kind),
    )


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
            logger.info("assistant confirmation missing step")
            raise Http404
        goal = step.goal
        raw = step.required_input if isinstance(step.required_input, dict) else {}
        owner = raw.get("actor_membership_id")
        if (
            goal.created_by_membership_id != actor.id
            or goal.conversation.created_by_membership_id != actor.id
            or owner != str(actor.id)
        ):
            logger.info("assistant confirmation owner mismatch")
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
            "the staff member does not want that prepared change, including a "
            "short decline. Do not propose that change again in the same reply. "
            "This does not confirm a change and does not delete a saved record. "
            "If nothing is waiting, the result says so."
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
        "read_declarations=%s limit_reached=%s capability=%s workflow=%s workflows=%s",
        model_calls,
        tool_calls,
        tools or "none",
        write_declarations,
        read_declarations,
        limit_reached,
        capability,
        workflow.get("workflow") or "none",
        workflow.get("active_workflows") or "none",
    )


def _system_instruction(base: str, *, pending: bool, active: list[str]) -> str:
    """Static prompt only. The gym brief, verified refs, and cue stay in contents."""

    parts = [base.strip()]
    if pending:
        parts.append("A confirmation is already waiting. Do not propose another write.")
    parts.append(_OPENING)
    parts.append(DOMAIN_MAP)
    parts.append("Workflows (call select_workflow to switch):\n" + catalog_text())
    if active:
        parts.append("Active workflow steps:\n" + guide_text(active))
    return "\n\n".join(part for part in parts if part)


def _workflow_declarations(
    offered: frozenset[str],
    contracts: Mapping[str, ToolContract],
    active: list[str],
) -> list[dict[str, object]]:
    selected = _model_declarations(
        offered,
        contracts,
        modules_for(active),
        all_reads=_workflows_all_reads(active),
    )
    selected.append(_select_declaration())
    selected.append(_cancel_declaration())
    return selected


def _select_declaration() -> dict[str, object]:
    return {
        "name": _SELECT,
        "description": (
            "Call this first to load the tools and steps for the staff member's "
            "task, and call it again whenever the task switches. The loaded tools "
            "cover only the active workflows. When the staff asks about an area "
            "those tools do not cover, call this with the current workflow and "
            "the workflow for that area (use reports for a lookup or question) "
            "before answering. Never say you cannot access something before "
            "doing that. Pass one or two workflow ids from the catalog."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "workflows": {
                    "type": "array",
                    "description": "One or two workflow ids.",
                    "maxItems": 2,
                    "items": {"type": "string", "enum": list(workflow_ids())},
                }
            },
            "required": ["workflows"],
        },
    }


def _select_workflows(
    workflow: dict[str, str],
    arguments: Mapping[str, object],
) -> dict[str, object]:
    choices = list(workflow_ids())
    raw = arguments.get("workflows")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 2:
        return {"error": "unknown_workflow", "choices": choices}
    chosen: list[str] = []
    for item in raw:
        if not isinstance(item, str) or get_workflow(item) is None:
            return {"error": "unknown_workflow", "choices": choices}
        if item not in chosen:
            chosen.append(item)
    workflow["active_workflows"] = ",".join(chosen)
    return {"status": "selected", "workflows": chosen}


def _active_workflows(workflow: Mapping[str, str]) -> list[str]:
    found: list[str] = []
    for item in (workflow.get("active_workflows") or "").split(","):
        name = item.strip()
        if name and get_workflow(name) is not None and name not in found:
            found.append(name)
    return found[:2]


def _auto_activate(workflow: dict[str, str], module: str) -> bool:
    """Load the first workflow that owns this module. Keep at most two, newest last."""

    matches = workflows_for_module(module)
    if not matches:
        return False
    chosen = matches[0].id
    current = _active_workflows(workflow)
    if chosen in current:
        return False
    current.append(chosen)
    workflow["active_workflows"] = ",".join(current[-2:])
    return True


def _declaration_names(declarations: list[dict[str, object]]) -> set[str]:
    names: set[str] = set()
    for item in declarations:
        name = item.get("name")
        if isinstance(name, str):
            names.add(name)
    return names


def _attach_context(contents: list[dict[str, object]], text: str) -> None:
    """Append the dynamic turn context to the last user message."""

    if text.strip() == "":
        return
    part = {"text": text}
    for item in reversed(contents):
        if item.get("role") != "user":
            continue
        parts = item.get("parts")
        if isinstance(parts, list):
            parts.append(part)
            return
    contents.append({"role": "user", "parts": [part]})


def _workflows_all_reads(active: list[str]) -> bool:
    for workflow_id in active:
        item = get_workflow(workflow_id)
        if item is not None and item.all_reads:
            return True
    return False


def _model_declarations(
    offered: frozenset[str],
    contracts: Mapping[str, ToolContract],
    modules: frozenset[str],
    all_reads: bool = False,
) -> list[dict[str, object]]:
    """Offer the tools in these modules that this staff member may use.

    A workflow marked all_reads also offers every registered read. Execution
    still checks permission, entitlement, and the gym on the request.
    """

    selected: list[dict[str, object]] = []
    for name in sorted(offered):
        contract = contracts.get(name)
        if contract is None:
            continue
        definition = contract.definition
        if definition.module not in modules and not (all_reads and definition.operation == "read"):
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


def _conversation_burmese(history: list[Message], staff_text: str, reply: str) -> bool:
    """The conversation language, not only the latest English side question.

    One Burmese question inside an English registration does not switch the
    proposal notice. A Burmese reply does, including when the latest question
    was asked in English.
    """

    if speaks_burmese(staff_text) or speaks_burmese(reply):
        return True
    prior = [
        message.body
        for message in history
        if message.role == Message.Role.STAFF
        and not is_state_message(message)
        and message.body.strip()
        and message.body.strip() != staff_text.strip()
    ]
    if not prior:
        return False
    burmese_count = sum(speaks_burmese(body) for body in prior)
    return burmese_count > len(prior) - burmese_count


def _needs_fresh_read(
    *,
    reply: str,
    staff_text: str,
    read_completed: bool,
    nudged: bool,
    declarations: list[dict[str, object]],
    contracts: Mapping[str, ToolContract],
    allowed: str,
) -> bool:
    """One retry when this turn answered stored facts without reading them.

    A date the staff already gave, or a quantity already in this turn's brief,
    is not a new lookup. An absence claim still is: the brief's total is not
    the per-record list the question asked for.
    """

    if nudged or read_completed or reply in {"", _LIMIT, _BLOCKED}:
        return False
    if asks_stored_date(staff_text):
        return False
    if not _declarations_have_read(declarations, contracts):
        return False
    return reply_needs_fresh_read(staff_text, reply, allowed)


def _declarations_have_read(
    declarations: list[dict[str, object]],
    contracts: Mapping[str, ToolContract],
) -> bool:
    for item in declarations:
        name = item.get("name")
        contract = contracts.get(name) if isinstance(name, str) else None
        if contract is not None and contract.definition.operation == "read":
            return True
    return False


def _localize_pending(
    proposal: dict[str, object],
    reply: str,
) -> tuple[dict[str, object], str]:
    """Show the proposal summary in the conversation language. The tool name stays."""

    summary = proposal.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return proposal, reply
    localized = localized_summary(summary, burmese=True)
    if localized == summary:
        return proposal, reply
    updated = dict(proposal)
    updated["summary"] = localized
    return updated, _replace_summary(reply, summary, localized)


def _replace_summary(reply: str, summary: str, localized: str) -> str:
    if summary in reply:
        return reply.replace(summary, localized, 1)
    lead = summary.removesuffix(".")
    if lead and lead in reply:
        return reply.replace(lead, localized.removesuffix("။"), 1)
    return reply


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


_APPROVE_AFTER_SUBMIT = {
    "submit_payment": ("approve_payment", "payment_id"),
    "submit_expense": ("approve_expense", "expense_id"),
    "submit_salary_period": ("approve_salary_period", "salary_period_id"),
    "submit_retail_sale": ("approve_retail_sale", "retail_sale_id"),
    "submit_pt_sale": ("approve_pt_sale", "pt_sale_id"),
}
_SUBMIT_AFTER_APPROVE = {
    "approve_payment": ("submit_payment", "payment_id"),
    "approve_expense": ("submit_expense", "expense_id"),
    "approve_salary_period": ("submit_salary_period", "salary_period_id"),
    "approve_retail_sale": ("submit_retail_sale", "retail_sale_id"),
    "approve_pt_sale": ("submit_pt_sale", "pt_sale_id"),
}
_Failure = tuple[str, dict[str, object], dict[str, object]]


def _safe_followup(
    name: str,
    body: Mapping[str, object],
    arguments: Mapping[str, object],
) -> tuple[str, dict[str, object]] | None:
    """The next confirmation, when it is one safe write and the ids are already known."""

    error = body.get("error")
    status = body.get("status") if isinstance(error, str) else ""
    if not isinstance(status, str):
        status = ""
    if error == "finance_state" and status == "PENDING_APPROVAL":
        pair = _APPROVE_AFTER_SUBMIT.get(name)
        if pair is not None:
            tool_name, field = pair
            record = arguments.get(field)
            if isinstance(record, str) and record:
                return tool_name, {field: record}
    if error == "not_pending" and status == "DRAFT":
        pair = _SUBMIT_AFTER_APPROVE.get(name)
        if pair is not None:
            tool_name, field = pair
            record = arguments.get(field)
            if isinstance(record, str) and record:
                return tool_name, {field: record}
    if error == "assignment_required":
        member = body.get("member_id")
        trainer = body.get("trainer_staff_profile_id")
        day = body.get("effective_from")
        if isinstance(member, str) and isinstance(trainer, str) and isinstance(day, str):
            return "create_assignment", {
                "member_id": member,
                "trainer_staff_profile_id": trainer,
                "effective_from": day,
            }
    return None


def _primary_failure(failures: list[_Failure]) -> _Failure:
    for item in failures:
        if _safe_followup(item[0], item[1], item[2]) is not None:
            return item
    for item in failures:
        status = item[1].get("status")
        if isinstance(item[1].get("error"), str) and isinstance(status, str) and status:
            return item
    return failures[-1]


def _finance_status(gym: Gym, tool_name: str, payload: Mapping[str, object]) -> str:
    pair = _APPROVE_AFTER_SUBMIT.get(tool_name) or _SUBMIT_AFTER_APPROVE.get(tool_name)
    if pair is None:
        return ""
    record = payload.get(pair[1])
    if not isinstance(record, str) or not record:
        return ""
    found = finance_record(gym, tool_name, record)
    return "" if found is None else found[0]


def _assignment_arguments(
    tool_name: str,
    payload: Mapping[str, object],
) -> dict[str, object] | None:
    if tool_name not in {"create_pt_sale", "create_pt_booking"}:
        return None
    member = payload.get("member_id")
    trainer = payload.get("trainer_staff_profile_id")
    raw = payload.get("service_date") if tool_name == "create_pt_sale" else payload.get("starts_at")
    if not isinstance(member, str) or not isinstance(trainer, str) or not isinstance(raw, str):
        return None
    day = raw[:10]
    if len(day) != 10:
        return None
    return {
        "member_id": member,
        "trainer_staff_profile_id": trainer,
        "effective_from": day,
    }


def _actor_may_self_approve(gym: Gym, actor: GymMembership) -> bool:
    from apps.tenants.models import GymMembershipRole

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        rows = GymMembershipRole.objects.filter(
            gym=gym,
            gym_membership=actor,
            role__is_active=True,
        ).select_related("role__source_template")
        keys = {
            row.role.key
            for row in rows
            if row.role.source_template is not None and row.role.source_template.key == row.role.key
        }
    return "OWNER" in keys or ("MANAGER" in keys and "RECEPTIONIST" in keys)


def _blocks_own_approval(
    gym: Gym,
    actor: GymMembership,
    tool_name: str,
    arguments: Mapping[str, object],
) -> bool:
    record = next((value for value in arguments.values() if isinstance(value, str)), None)
    if record is None:
        return False
    found = finance_record(gym, tool_name, record)
    if found is None:
        return False
    _status, created, submitted = found
    actor_id = str(actor.id)
    if actor_id not in {created, submitted}:
        return False
    return not _actor_may_self_approve(gym, actor)


def _allowed_followup(
    *,
    gym: Gym,
    actor: GymMembership,
    offered: frozenset[str],
    follow: tuple[str, dict[str, object]] | None,
) -> tuple[tuple[str, dict[str, object]] | None, bool]:
    if follow is None:
        return None, True
    tool_name, arguments = follow
    if tool_name.startswith("approve_") and (
        tool_name not in offered or _blocks_own_approval(gym, actor, tool_name, arguments)
    ):
        return None, False
    if tool_name not in offered:
        return None, True
    return follow, True


def _explain_failure(
    *,
    request: Request,
    view: APIView,
    gym: Gym,
    actor: GymMembership,
    conversation: Conversation,
    offered: frozenset[str],
    staff_text: str,
    workflow: dict[str, str],
    failures: list[_Failure],
) -> tuple[str, dict[str, object] | None, bool]:
    name, body, arguments = _primary_failure(failures)
    follow, can_continue = _allowed_followup(
        gym=gym,
        actor=actor,
        offered=offered,
        follow=_safe_followup(name, body, arguments),
    )
    proposal: dict[str, object] | None = None
    opened = False
    if follow is not None:
        card, _follow_body = _handle_call(
            request=request,
            view=view,
            gym=gym,
            actor=actor,
            conversation=conversation,
            name=follow[0],
            arguments=follow[1],
            offered=offered,
            proposal=None,
            saved=None,
            staff_text=staff_text,
            workflow=workflow,
        )
        if card is not None:
            proposal = card
            opened = True
    status = body.get("status")
    if (
        body.get("error") in {"finance_state", "not_pending", "payment_state"}
        and isinstance(status, str)
        and status
    ):
        text = recovery_sentence(body, staff_text, can_continue=can_continue)
    else:
        text = staff_notice(body, staff_text) or recovery_sentence(
            body,
            staff_text,
            can_continue=can_continue,
        )
    return text, proposal, opened


def _recovered_confirmation(
    *,
    request: Request,
    view: APIView,
    gym: Gym,
    actor: GymMembership,
    goal: Goal,
    step: WorkflowStep,
    body: dict[str, object],
    follow: tuple[str, dict[str, object]] | None,
) -> dict[str, object]:
    error = body.get("error")
    summary = error if isinstance(error, str) else "rejected"
    close_goal(
        gym=gym,
        goal=goal,
        step=step,
        status=Goal.Status.CANCELLED,
        step_status=WorkflowStep.Status.CANCELLED,
        summary=summary,
    )
    staff_text = _staff_text(_history(gym, goal.conversation))
    chosen, can_continue = _allowed_followup(
        gym=gym,
        actor=actor,
        offered=_offered(request),
        follow=follow,
    )
    pending = None
    if chosen is not None:
        pending, _follow_body = _handle_call(
            request=request,
            view=view,
            gym=gym,
            actor=actor,
            conversation=goal.conversation,
            name=chosen[0],
            arguments=chosen[1],
            offered=_offered(request),
            proposal=None,
            saved=None,
            staff_text=staff_text,
            workflow=load_state(gym, goal.conversation),
        )
    text = recovery_sentence(body, staff_text, can_continue=can_continue)
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
        "pending_confirmation": pending,
        "tool_activity": [public_activity(step.tool_name, "cancelled")],
        "outcome": "cancelled",
    }


def _confirmation_text(
    gym: Gym,
    conversation: Conversation,
    kind: str,
    detail: Mapping[str, object] | None = None,
) -> str:
    staff_text = _staff_text(_history(gym, conversation))
    return gate_sentence(kind, staff_text, detail)


def _stock_cost(name: str, payload: dict[str, object]) -> dict[str, object] | None:
    """A purchase needs a unit cost. Other movements do not store one."""

    if name != "adjust_stock":
        return None
    movement = payload.get("movement_type")
    kind = movement.strip().upper() if isinstance(movement, str) else ""
    cost = payload.get("unit_cost")
    has_cost = cost not in {None, ""}
    if kind == "PURCHASE" and not has_cost:
        return {"error": "missing_fields", "fields": ["unit_cost"]}
    if kind != "PURCHASE" and has_cost:
        payload.pop("unit_cost", None)
    return None


def _wage_lines(name: str, payload: Mapping[str, object]) -> dict[str, object] | None:
    """A salary line needs the person, amount, currency, method, and description."""

    if name != "replace_salary_lines":
        return None
    lines = payload.get("lines")
    if not isinstance(lines, list) or not lines:
        return {"error": "missing_fields", "fields": ["lines"]}
    required = ("staff_profile_id", "amount", "currency", "payment_method", "description")
    missing: list[str] = []
    for line in lines:
        if not isinstance(line, dict):
            return {"error": "missing_fields", "fields": ["lines"]}
        for key in required:
            if line.get(key) in {None, ""}:
                missing.append(key)
    if missing:
        return {"error": "missing_fields", "fields": list(dict.fromkeys(missing))}
    return None


def _validation_hint(exc: ValidationError) -> str:
    """Name the fields the serializer rejected. Values stay out of the reply."""

    detail = exc.detail
    if not isinstance(detail, dict):
        return ""
    names = [
        key
        for key in detail
        if isinstance(key, str) and key.replace("_", "").isalnum() and len(key) <= 40
    ]
    if not names:
        return ""
    return "Check " + ", ".join(names[:4]) + "."


def _validation_kind(exc: ValidationError) -> str:
    blob = str(exc.detail).casefold()
    if "stock" in blob or "not enough" in blob:
        return "stock_short"
    if "business date" in blob or "future" in blob:
        return "date_invalid"
    if "already exists" in blob or "already stored" in blob:
        return "already_saved"
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
