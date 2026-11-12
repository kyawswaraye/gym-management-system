"use client";

import { type FormEvent, useEffect, useState } from "react";

import {
  listAnnouncements,
  operationMutation,
  OperationError,
} from "@/features/operations/client";
import { useTx } from "@/i18n/use-tx";
import { actionButtonClass } from "@/lib/ui/action-button";
import type { Announcement, AnnouncementWriteRequest } from "@/types/api";

const TYPES = ["EVENT", "PROMOTION", "GENERAL"] as const;
const AUDIENCES = [
  "ALL_ACTIVE_MEMBERS",
  "PT_CLIENTS",
  "EXPIRING_WITHIN_7_DAYS",
  "EXPIRING_WITHIN_30_DAYS",
] as const;

const TYPE_LABEL = {
  EVENT: "Event",
  PROMOTION: "Promotion",
  GENERAL: "General",
} as const;

const AUDIENCE_LABEL = {
  ALL_ACTIVE_MEMBERS: "All active members",
  PT_CLIENTS: "PT clients",
  EXPIRING_WITHIN_7_DAYS: "Expiring within 7 days",
  EXPIRING_WITHIN_30_DAYS: "Expiring within 30 days",
} as const;

type Draft = {
  type: AnnouncementWriteRequest["type"];
  title: string;
  body: string;
  audience: AnnouncementWriteRequest["audience"];
};

const EMPTY: Draft = {
  type: "GENERAL",
  title: "",
  body: "",
  audience: "ALL_ACTIVE_MEMBERS",
};

export function AnnouncementsManager({ canManage }: { canManage: boolean }) {
  const tx = useTx();
  const [rows, setRows] = useState<Announcement[]>([]);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [editing, setEditing] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function reload() {
    const page = await listAnnouncements({ limit: 25 });
    setRows(page.data);
  }

  useEffect(() => {
    let active = true;
    void listAnnouncements({ limit: 25 })
      .then((page) => {
        if (active) setRows(page.data);
      })
      .catch((error: unknown) => {
        if (active) {
          setMessage(
            error instanceof OperationError
              ? error.message
              : "The announcements could not be loaded.",
          );
        }
      });
    return () => {
      active = false;
    };
  }, []);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!canManage) return;
    setBusy(true);
    setMessage(null);
    try {
      const payload = {
        type: draft.type,
        title: draft.title.trim(),
        body: draft.body.trim(),
        audience: draft.audience,
      };
      if (editing) {
        await operationMutation(`announcements/${editing}/`, payload, "PATCH");
      } else {
        await operationMutation("announcements/", payload);
      }
      setDraft(EMPTY);
      setEditing(null);
      await reload();
    } catch (error) {
      setMessage(
        error instanceof OperationError
          ? error.message
          : "The announcement could not be saved.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function command(id: string, action: "publish" | "cancel") {
    if (!canManage) return;
    setBusy(true);
    setMessage(null);
    try {
      await operationMutation(`announcements/${id}/${action}/`, {});
      await reload();
    } catch (error) {
      setMessage(
        error instanceof OperationError
          ? error.message
          : "The announcement could not be updated.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-6">
      {message ? (
        <p className="text-body text-danger-text" role="alert">
          {message}
        </p>
      ) : null}
      {canManage ? (
        <form className="grid gap-4" onSubmit={(event) => void onSubmit(event)}>
          <h2 className="text-title">
            {tx(editing ? "Edit draft" : "New announcement")}
          </h2>
          <label className="field">
            {tx("General")}
            <select
              className="input"
              value={draft.type}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  type: event.target.value as Draft["type"],
                }))
              }
            >
              {TYPES.map((type) => (
                <option key={type} value={type}>
                  {tx(TYPE_LABEL[type])}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            {tx("Announcement title")}
            <input
              className="input"
              maxLength={120}
              required
              value={draft.title}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  title: event.target.value,
                }))
              }
            />
          </label>
          <label className="field">
            {tx("Announcement body")}
            <textarea
              className="input"
              maxLength={4000}
              required
              rows={4}
              value={draft.body}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  body: event.target.value,
                }))
              }
            />
          </label>
          <label className="field">
            {tx("All active members")}
            <select
              className="input"
              value={draft.audience}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  audience: event.target.value as Draft["audience"],
                }))
              }
            >
              {AUDIENCES.map((audience) => (
                <option key={audience} value={audience}>
                  {tx(AUDIENCE_LABEL[audience])}
                </option>
              ))}
            </select>
          </label>
          <button
            className={actionButtonClass(
              editing ? "Update draft" : "Save draft",
            )}
            disabled={busy}
            type="submit"
          >
            {tx(editing ? "Update draft" : "Save draft")}
          </button>
        </form>
      ) : null}
      {rows.length === 0 ? (
        <p className="text-body text-muted">{tx("No announcements yet.")}</p>
      ) : (
        <ul className="grid gap-3">
          {rows.map((row) => (
            <li className="rounded-lg border border-border p-4" key={row.id}>
              <p className="text-body-strong">{row.title}</p>
              <p className="text-body text-muted">
                {row.status} · {tx(TYPE_LABEL[row.type])} ·{" "}
                {tx(AUDIENCE_LABEL[row.audience])}
              </p>
              <p className="text-body whitespace-pre-wrap">{row.body}</p>
              {canManage && row.status === "DRAFT" ? (
                <div className="mt-3 flex gap-2">
                  <button
                    className={actionButtonClass("Edit")}
                    disabled={busy}
                    type="button"
                    onClick={() => {
                      setEditing(row.id);
                      setDraft({
                        type: row.type,
                        title: row.title,
                        body: row.body,
                        audience: row.audience,
                      });
                    }}
                  >
                    {tx("Edit draft")}
                  </button>
                  <button
                    className={actionButtonClass("Publish")}
                    disabled={busy}
                    type="button"
                    onClick={() => void command(row.id, "publish")}
                  >
                    {tx("Publish")}
                  </button>
                  <button
                    className={actionButtonClass("Cancel")}
                    disabled={busy}
                    type="button"
                    onClick={() => void command(row.id, "cancel")}
                  >
                    {tx("Cancel")}
                  </button>
                </div>
              ) : null}
              {canManage &&
              (row.status === "SCHEDULED" || row.status === "PUBLISHED") ? (
                <div className="mt-3">
                  <button
                    className={actionButtonClass("Cancel")}
                    disabled={busy}
                    type="button"
                    onClick={() => void command(row.id, "cancel")}
                  >
                    {tx("Cancel")}
                  </button>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
