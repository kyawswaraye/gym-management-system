"use client";

import { FormEvent, useEffect, useLayoutEffect, useRef, useState } from "react";

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

type ChatMessage = {
  id: string;
  role: string;
  body: string;
};

function fieldLabel(name: string) {
  return name.replaceAll("_", " ");
}

type SaleLine = {
  product: string;
  quantity: string;
  discount: string;
};

function saleLines(fields: { name: string; value: string }[]): SaleLine[] {
  const rows = new Map<number, SaleLine>();
  for (const field of fields) {
    const match = /^lines\.(\d+)\.(product|quantity|discount)$/.exec(
      field.name,
    );
    if (!match) continue;
    const index = Number(match[1]);
    const row = rows.get(index) ?? { product: "", quantity: "", discount: "" };
    row[match[2] as "product" | "quantity" | "discount"] = field.value;
    rows.set(index, row);
  }
  return [...rows.keys()]
    .sort((left, right) => left - right)
    .map(
      (index) => rows.get(index) ?? { product: "", quantity: "", discount: "" },
    );
}

function isSaleLineField(name: string) {
  return /^lines\.\d+\.(product|quantity|discount)$/.test(name);
}

const HISTORY_LABELS = [
  "Today",
  "Yesterday",
  "Previous 7 days",
  "Older",
] as const;

function historyBuckets(rows: ConversationSummary[]) {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const today = start.getTime();
  const day = 86_400_000;
  const buckets: Record<
    (typeof HISTORY_LABELS)[number],
    ConversationSummary[]
  > = {
    Today: [],
    Yesterday: [],
    "Previous 7 days": [],
    Older: [],
  };
  for (const row of rows) {
    const time = new Date(row.created_at).getTime();
    if (Number.isNaN(time) || time >= today) buckets.Today.push(row);
    else if (time >= today - day) buckets.Yesterday.push(row);
    else if (time >= today - 7 * day) buckets["Previous 7 days"].push(row);
    else buckets.Older.push(row);
  }
  return HISTORY_LABELS.filter((label) => buckets[label].length > 0).map(
    (label) => ({ label, rows: buckets[label] }),
  );
}

function TypingIndicator({ label }: { label: string }) {
  return (
    <p className="flex items-center gap-1 py-1">
      <span className="sr-only">{label}</span>
      {[0, 1, 2].map((dot) => (
        <span
          key={dot}
          aria-hidden="true"
          className="size-1.5 animate-bounce rounded-full bg-[#8e8ea0] motion-reduce:animate-none"
          style={{ animationDelay: `${dot * 0.15}s` }}
        />
      ))}
    </p>
  );
}

function HistoryIcon() {
  return (
    <svg aria-hidden="true" className="size-5" fill="none" viewBox="0 0 24 24">
      <path
        d="M4 12a8 8 0 1 0 2.3-5.6"
        stroke="currentColor"
        strokeLinecap="round"
        strokeWidth="1.8"
      />
      <path
        d="M4 4.5V8h3.5"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
      <path
        d="M12 8v4.2l2.6 1.6"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </svg>
  );
}

function ComposeIcon() {
  return (
    <svg aria-hidden="true" className="size-5" fill="none" viewBox="0 0 24 24">
      <path
        d="M12 5v14M5 12h14"
        stroke="currentColor"
        strokeLinecap="round"
        strokeWidth="1.8"
      />
    </svg>
  );
}

function SendIcon() {
  return (
    <svg aria-hidden="true" className="size-4" fill="none" viewBox="0 0 24 24">
      <path
        d="M12 19V5M6 11l6-6 6 6"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
    </svg>
  );
}

export function AssistantChat({ membershipId }: { membershipId: string }) {
  const tx = useTx();
  const [available, setAvailable] = useState<boolean | null>(null);
  const [notEntitled, setNotEntitled] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pending, setPending] = useState<PendingConfirmation | null>(null);
  const [activity, setActivity] = useState<ToolActivity[]>([]);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const skipRestore = useRef(false);

  useEffect(() => {
    let cancelled = false;
    loadStatus()
      .then(async (status) => {
        if (cancelled) return;
        const blocked = status.reasons.includes("not_entitled");
        setNotEntitled(blocked);
        setAvailable(status.available);
        if (blocked) return;
        const rows = await loadConversations();
        if (cancelled) return;
        const mine = rows.filter(
          (row) => row.created_by_membership_id === membershipId,
        );
        setConversations(mine);
        if (skipRestore.current) return;
        const chosen = mine[0];
        if (!chosen) return;
        const detail = await loadConversation(chosen.id);
        if (cancelled || skipRestore.current) return;
        setConversationId(detail.id);
        setMessages(detail.messages);
        setPending(detail.pending_confirmation);
        setActivity([]);
      })
      .catch(() => {
        if (!cancelled) setAvailable(false);
      });
    return () => {
      cancelled = true;
    };
  }, [membershipId]);

  useEffect(() => {
    if (!historyOpen) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setHistoryOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [historyOpen]);

  useLayoutEffect(() => {
    const log = logRef.current;
    if (!log) return;
    log.scrollTop = log.scrollHeight;
  }, [messages, busy, pending, error, activity]);

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

  function startConversation() {
    skipRestore.current = true;
    setConversationId(null);
    setMessages([]);
    setPending(null);
    setActivity([]);
    setError("");
    setHistoryOpen(false);
  }

  async function openConversation(id: string) {
    setBusy(true);
    setError("");
    setHistoryOpen(false);
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
    skipRestore.current = true;
    const prior = [
      ...messages,
      { id: crypto.randomUUID(), role: "staff", body: message },
    ];
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
  const lines = pending ? saleLines(pending.fields) : [];
  const detailFields = pending
    ? pending.fields.filter((field) => !isSaleLineField(field.name))
    : [];
  const started = messages.length > 0;
  const buckets = historyBuckets(conversations);
  const iconButton =
    "grid size-9 place-items-center rounded-lg text-[#0d0d0d] hover:bg-[#f4f4f4]";
  const rowButton =
    "block w-full min-w-0 truncate rounded-lg px-3 py-2 text-left text-sm text-[#0d0d0d] hover:bg-[#ececec]";

  const dock = (
    <div className="grid gap-3">
      {notEntitled ? (
        <p
          className="rounded-xl bg-[#fff1f1] px-3 py-2 text-sm text-[#b4232c]"
          role="alert"
        >
          {tx(
            "AI Assistant is not included in this gym's subscription. Ask your GymPilot administrator to enable it.",
          )}
        </p>
      ) : available === false ? (
        <p
          className="rounded-xl bg-[#fff1f1] px-3 py-2 text-sm text-[#b4232c]"
          role="alert"
        >
          {tx("The assistant is unavailable.")}
        </p>
      ) : null}
      {pending ? (
        <div
          className="grid gap-3 rounded-2xl border border-[#e5e5e5] bg-white p-4"
          role="region"
        >
          <p className="text-[15px] leading-6 text-[#0d0d0d]">
            {pending.summary}
          </p>
          {amount ? (
            <p className="text-sm font-semibold text-[#0d0d0d]">
              {tx("Amount")} {amount}
            </p>
          ) : null}
          {pending.approval ? (
            <p className="text-sm text-[#6e6e80]">
              {tx("Record")} {pending.approval.record_id}
            </p>
          ) : null}
          {lines.length > 0 ? (
            <ol className="grid gap-2">
              {lines.map((line, index) => (
                <li
                  key={index}
                  className="rounded-xl bg-[#f4f4f4] px-3 py-2 text-sm text-[#0d0d0d]"
                >
                  <p>{line.product || tx("Product unavailable")}</p>
                  {line.quantity ? (
                    <p>
                      {tx("Quantity")} {line.quantity}
                    </p>
                  ) : null}
                  {line.discount ? (
                    <p>
                      {tx("Discount")} {line.discount}
                    </p>
                  ) : null}
                </li>
              ))}
            </ol>
          ) : null}
          {detailFields.length > 0 ? (
            <dl className="grid gap-1 text-sm">
              {detailFields.map((field) => (
                <div
                  key={field.name}
                  className="grid grid-cols-[8rem_minmax(0,1fr)] gap-2"
                >
                  <dt className="text-[#6e6e80]">{fieldLabel(field.name)}</dt>
                  <dd className="truncate text-[#0d0d0d]">{field.value}</dd>
                </div>
              ))}
            </dl>
          ) : null}
          <div className="flex flex-col gap-2 sm:flex-row">
            <button
              type="button"
              className="h-9 rounded-full bg-[#0d0d0d] px-4 text-sm text-white disabled:opacity-40"
              disabled={busy}
              onClick={() => void decide("confirm")}
            >
              {tx("Confirm change")}
            </button>
            <button
              type="button"
              className="h-9 rounded-full border border-[#d9d9d9] bg-white px-4 text-sm text-[#0d0d0d] hover:bg-[#f4f4f4] disabled:opacity-40"
              disabled={busy}
              onClick={() => void decide("reject")}
            >
              {tx("Reject change")}
            </button>
          </div>
        </div>
      ) : null}
      {error ? (
        <p
          className="rounded-xl bg-[#fff1f1] px-3 py-2 text-sm text-[#b4232c]"
          role="alert"
        >
          {error}
        </p>
      ) : null}
      <form
        className="assistant-composer flex items-end gap-2 rounded-[1.75rem] border border-[#e5e5e5] bg-white px-3 py-1.5 shadow-[0_8px_24px_rgb(0_0_0/6%)]"
        onSubmit={(event) => void onSubmit(event)}
      >
        <label className="sr-only" htmlFor="assistant-message">
          {tx("Assistant")}
        </label>
        <textarea
          id="assistant-message"
          className="max-h-40 min-h-11 flex-1 resize-none bg-transparent px-1 py-2.5 text-[16px] leading-6 text-[#0d0d0d] outline-none placeholder:text-[#8e8ea0]"
          value={draft}
          rows={1}
          maxLength={4000}
          placeholder={tx("Ask anything")}
          disabled={busy || available === false || notEntitled}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
        />
        <button
          type="submit"
          className="mb-1.5 grid size-8 shrink-0 place-items-center rounded-full bg-[#0d0d0d] text-white disabled:bg-[#f4f4f4] disabled:text-[#8e8ea0]"
          aria-label={tx("Send")}
          disabled={
            busy || available === false || notEntitled || draft.trim() === ""
          }
        >
          <SendIcon />
        </button>
      </form>
    </div>
  );

  return (
    <section className="assistant-stage relative flex min-h-0 flex-1 flex-col text-[#0d0d0d]">
      <header className="assistant-toolbar relative flex h-14 shrink-0 items-center gap-1 bg-white/80 px-2 backdrop-blur-sm">
        <button
          type="button"
          className={iconButton}
          aria-label={tx("History")}
          aria-expanded={historyOpen}
          aria-controls="assistant-history"
          onClick={() => setHistoryOpen((open) => !open)}
        >
          <HistoryIcon />
        </button>
        <p className="pointer-events-none absolute left-1/2 -translate-x-1/2 text-sm font-medium text-[#0d0d0d]">
          {tx("Assistant")}
        </p>
        <button
          type="button"
          className={`${iconButton} ml-auto`}
          aria-label={tx("New conversation")}
          onClick={startConversation}
        >
          <ComposeIcon />
        </button>
      </header>
      {historyOpen ? (
        <button
          type="button"
          className="absolute inset-0 z-10 bg-black/25"
          aria-label={tx("Close history")}
          onClick={() => setHistoryOpen(false)}
        />
      ) : null}
      {historyOpen ? (
        <aside
          id="assistant-history"
          className="absolute inset-y-0 left-0 z-20 flex w-[min(18rem,86%)] min-w-0 flex-col overflow-x-hidden overflow-y-auto border-r border-[#ececec] bg-[#f9f9f9] px-2 py-3 shadow-[4px_0_24px_rgb(0_0_0/6%)]"
        >
          <p className="px-3 pb-2 text-xs font-medium text-[#8e8ea0]">
            {tx("Your conversations")}
          </p>
          <button
            type="button"
            className={`${rowButton} font-medium`}
            onClick={startConversation}
          >
            {tx("New conversation")}
          </button>
          {buckets.length === 0 ? (
            <p className="px-3 py-2 text-sm leading-6 text-[#6e6e80]">
              {tx("No conversation yet.")}
            </p>
          ) : (
            buckets.map((bucket) => (
              <div key={bucket.label} className="mt-3">
                <p className="px-3 pb-1 text-xs font-medium text-[#8e8ea0]">
                  {tx(bucket.label)}
                </p>
                <ul className="grid min-w-0 gap-0.5">
                  {bucket.rows.map((row) => (
                    <li key={row.id} className="min-w-0">
                      <button
                        type="button"
                        className={
                          row.id === conversationId
                            ? `${rowButton} bg-[#e5e5e5]`
                            : rowButton
                        }
                        aria-current={
                          row.id === conversationId ? "true" : undefined
                        }
                        onClick={() => void openConversation(row.id)}
                      >
                        {row.title || tx("Assistant")}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ))
          )}
        </aside>
      ) : null}
      <div
        className={
          started
            ? "flex min-h-0 flex-1 flex-col"
            : "flex min-h-0 flex-1 flex-col items-center justify-center px-4 pb-16"
        }
      >
        <div
          ref={logRef}
          className={
            started
              ? "min-h-0 flex-1 overflow-y-auto"
              : "w-full max-w-3xl text-center"
          }
          role="log"
        >
          {started ? (
            <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-6">
              {messages.map((message) => {
                if (message.body.startsWith("workflow-state v1")) return null;
                const own = message.role === "staff";
                if (own) {
                  return (
                    <p
                      key={message.id}
                      className="max-w-[min(100%,32rem)] self-end rounded-[1.5rem] bg-white px-4 py-2.5 text-[16px] leading-7 break-words whitespace-pre-wrap text-[#0d0d0d] shadow-[0_1px_2px_rgb(18_57_122/10%)]"
                    >
                      {message.body}
                    </p>
                  );
                }
                return (
                  <p
                    key={message.id}
                    className="text-[16px] leading-7 break-words whitespace-pre-wrap text-[#0d0d0d]"
                  >
                    {message.body}
                  </p>
                );
              })}
              {busy ? <TypingIndicator label={tx("Working…")} /> : null}
            </div>
          ) : (
            <>
              <h1 className="text-[1.75rem] font-normal tracking-tight text-[#0d0d0d]">
                {tx("Assistant")}
              </h1>
              <p className="mx-auto mt-3 max-w-md text-[15px] leading-6 text-[#6e6e80]">
                {tx("Ask about this gym. Changes wait for your confirmation.")}
              </p>
              {busy ? (
                <div className="mt-4 flex justify-center">
                  <TypingIndicator label={tx("Working…")} />
                </div>
              ) : null}
            </>
          )}
        </div>
        <div
          className={
            started
              ? "mx-auto w-full max-w-3xl px-4 pt-2 pb-4"
              : "mt-6 w-full max-w-3xl"
          }
        >
          {dock}
        </div>
      </div>
    </section>
  );
}
