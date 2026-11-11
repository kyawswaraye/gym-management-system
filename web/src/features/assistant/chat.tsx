"use client";

import { FormEvent, useEffect, useState } from "react";

import {
  confirmationAmount,
  confirmStep,
  loadConversation,
  loadConversations,
  loadStatus,
  rejectStep,
  sendTurn,
  type ConversationSummary,
  type PendingConfirmation,
  type ToolActivity,
  type TurnResult,
} from "@/features/assistant/client";
import { OperationError } from "@/features/operations/client";
import { useTx } from "@/i18n/use-tx";
import { actionButtonClass } from "@/lib/ui/action-button";

type ChatMessage = {
  id: string;
  role: string;
  body: string;
};

export function AssistantChat({ membershipId }: { membershipId: string }) {
  const tx = useTx();
  const [available, setAvailable] = useState<boolean | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pending, setPending] = useState<PendingConfirmation | null>(null);
  const [activity, setActivity] = useState<ToolActivity[]>([]);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    loadStatus()
      .then((status) => {
        if (!cancelled) setAvailable(status.available);
      })
      .catch(() => {
        if (!cancelled) setAvailable(false);
      });
    loadConversations()
      .then((rows) => {
        if (!cancelled) {
          setConversations(
            rows.filter((row) => row.created_by_membership_id === membershipId),
          );
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [membershipId]);

  function applyTurn(result: TurnResult, prior: ChatMessage[]) {
    setConversationId(result.conversation_id);
    setMessages([...prior, result.message]);
    setPending(result.pending_confirmation);
    setActivity(result.tool_activity);
    setConversations((current) =>
      current.some((row) => row.id === result.conversation_id)
        ? current
        : [
            {
              id: result.conversation_id,
              status: "open",
              title: prior[0]?.body ?? result.message.body,
              created_by_membership_id: membershipId,
              created_at: result.message.created_at,
            },
            ...current,
          ],
    );
  }

  async function openConversation(id: string) {
    setBusy(true);
    setError("");
    try {
      const detail = await loadConversation(id);
      setConversationId(detail.id);
      setMessages(detail.messages);
      setPending(detail.pending_confirmation);
      setActivity([]);
    } catch (caught) {
      setError(
        caught instanceof OperationError
          ? caught.message
          : tx("The assistant is unavailable."),
      );
    } finally {
      setBusy(false);
    }
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = draft.trim();
    if (!message || busy || available === false) return;
    const prior = [...messages, { id: "draft", role: "staff", body: message }];
    setMessages(prior);
    setDraft("");
    setBusy(true);
    setError("");
    try {
      applyTurn(await sendTurn(message, conversationId), prior);
    } catch (caught) {
      setError(
        caught instanceof OperationError
          ? caught.message
          : tx("The assistant is unavailable."),
      );
    } finally {
      setBusy(false);
    }
  }

  async function decide(kind: "confirm" | "reject") {
    if (!pending || busy) return;
    setBusy(true);
    setError("");
    try {
      const result =
        kind === "confirm"
          ? await confirmStep(pending.step_id)
          : await rejectStep(pending.step_id);
      applyTurn(result, messages);
    } catch (caught) {
      setError(
        caught instanceof OperationError
          ? caught.message
          : tx("The assistant is unavailable."),
      );
    } finally {
      setBusy(false);
    }
  }

  const amount = pending ? confirmationAmount(pending) : null;

  return (
    <div className="grid gap-4 lg:grid-cols-[16rem_minmax(0,1fr)]">
      <aside className="card grid content-start gap-2 p-3">
        <button
          type="button"
          className={actionButtonClass("New conversation")}
          onClick={() => {
            setConversationId(null);
            setMessages([]);
            setPending(null);
            setActivity([]);
            setError("");
          }}
        >
          {tx("New conversation")}
        </button>
        <p className="muted text-sm">{tx("Your conversations")}</p>
        {conversations.length === 0 ? (
          <p className="muted text-sm">{tx("No conversation yet.")}</p>
        ) : (
          <ul className="grid gap-1">
            {conversations.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  className="w-full truncate rounded px-2 py-1 text-left text-sm hover:bg-surface"
                  onClick={() => void openConversation(row.id)}
                >
                  {row.title || tx("Assistant")}
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>
      <section
        className="card grid gap-3 p-3"
        aria-labelledby="assistant-title"
      >
        {available === false ? (
          <p className="error" role="alert">
            {tx("The assistant is unavailable.")}
          </p>
        ) : null}
        <div className="grid max-h-[28rem] gap-2 overflow-y-auto" role="log">
          {messages.length === 0 ? (
            <p className="muted text-sm">{tx("No conversation yet.")}</p>
          ) : (
            messages.map((message) => (
              <p
                key={message.id}
                className={
                  message.role === "staff"
                    ? "justify-self-end max-w-[40rem] rounded bg-surface px-3 py-2 text-sm"
                    : "max-w-[40rem] text-sm"
                }
              >
                {message.body}
              </p>
            ))
          )}
        </div>
        {activity.length > 0 ? (
          <p className="muted text-xs">
            {activity.map((item) => `${item.name} ${item.status}`).join(", ")}
          </p>
        ) : null}
        {pending ? (
          <div
            className="grid gap-2 rounded border border-border p-3"
            role="region"
          >
            <p className="text-sm">{pending.summary}</p>
            {amount ? (
              <p className="text-title">
                {tx("Amount")} {amount}
              </p>
            ) : null}
            {pending.approval ? (
              <p className="muted text-sm">
                {tx("Record")} {pending.approval.record_id}
              </p>
            ) : null}
            {pending.fields.length > 0 ? (
              <dl className="grid gap-1 text-sm">
                {pending.fields.map((field) => (
                  <div
                    key={field.name}
                    className="grid grid-cols-[8rem_minmax(0,1fr)] gap-2"
                  >
                    <dt className="muted">{field.name}</dt>
                    <dd className="truncate">{field.value}</dd>
                  </div>
                ))}
              </dl>
            ) : null}
            <div className="flex flex-col gap-2 sm:flex-row">
              <button
                type="button"
                className={actionButtonClass("Confirm change")}
                disabled={busy}
                onClick={() => void decide("confirm")}
              >
                {tx("Confirm change")}
              </button>
              <button
                type="button"
                className={actionButtonClass("Reject change")}
                disabled={busy}
                onClick={() => void decide("reject")}
              >
                {tx("Reject change")}
              </button>
            </div>
          </div>
        ) : null}
        {error ? (
          <p className="error" role="alert">
            {error}
          </p>
        ) : null}
        {busy ? <p className="muted text-sm">{tx("Working…")}</p> : null}
        <form
          className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]"
          onSubmit={(event) => void onSubmit(event)}
        >
          <label className="sr-only" htmlFor="assistant-message">
            {tx("Assistant")}
          </label>
          <textarea
            id="assistant-message"
            className="input min-h-16"
            value={draft}
            maxLength={4000}
            disabled={busy || available === false}
            onChange={(event) => setDraft(event.target.value)}
          />
          <button
            type="submit"
            className={actionButtonClass("Send")}
            disabled={busy || available === false || draft.trim() === ""}
          >
            {tx("Send")}
          </button>
        </form>
      </section>
    </div>
  );
}
