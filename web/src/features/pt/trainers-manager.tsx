"use client";

import { useEffect, useRef, useState } from "react";

import {
  Alert,
  CursorControls,
  Empty,
  Field,
  Loading,
  ReasonField,
  TextInput,
} from "@/features/finance/ui";
import { useCursorPages } from "@/features/finance/paging";
import {
  cancelAssignment,
  createAssignment,
  endAssignment,
  listAssignedMembers,
  listAssignments,
  listTrainers,
} from "@/features/pt/client";
import { phase5Error } from "@/features/pt/errors";
import { useLookupOptions } from "@/features/pt/lookup";
import {
  MemberSearch,
  memberLabel,
  type PickedMember,
} from "@/features/pt/member-search";
import type {
  AssignedMember,
  TrainerAssignment,
  TrainerIdentity,
} from "@/types/api";

const PAGE = "25";

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

function statusClass(status: string): string {
  if (status === "ACTIVE")
    return "bg-emerald-50 text-emerald-700 ring-emerald-600/20";
  if (status === "ENDED")
    return "bg-slate-100 text-slate-600 ring-slate-500/20";
  return "bg-rose-50 text-rose-700 ring-rose-600/20";
}

export function TrainersManager({
  canCreate,
  canClose,
  today,
}: {
  canCreate: boolean;
  canClose: boolean;
  today: string;
}) {
  const [status, setStatus] = useState("");
  const [trainerId, setTrainerId] = useState("");
  const [memberFilter, setMemberFilter] = useState<PickedMember | null>(null);
  const [selectedTrainer, setSelectedTrainer] =
    useState<TrainerIdentity | null>(null);
  const [member, setMember] = useState<PickedMember | null>(null);
  const [notice, setNotice] = useState("");
  const [commandError, setCommandError] = useState("");
  const [pending, setPending] = useState(false);
  const [active, setActive] = useState<TrainerAssignment | null>(null);
  const [mode, setMode] = useState<"create" | "end" | "cancel" | null>(null);
  const pendingRef = useRef(false);
  const createDialogRef = useRef<HTMLFormElement>(null);

  const trainers = useCursorPages<TrainerIdentity>("trainers", (after) =>
    listTrainers({ limit: PAGE, ...(after ? { after } : {}) }),
  );
  const assignments = useCursorPages<TrainerAssignment>(
    `assignments:${status}:${trainerId}:${memberFilter?.id ?? ""}`,
    (after) =>
      listAssignments({
        limit: PAGE,
        ...(after ? { after } : {}),
        ...(status ? { status } : {}),
        ...(trainerId ? { trainer_staff_profile_id: trainerId } : {}),
        ...(memberFilter ? { member_id: memberFilter.id } : {}),
      }),
  );
  const assigned = useCursorPages<AssignedMember>(
    selectedTrainer ? `assigned:${selectedTrainer.id}` : "assigned:none",
    (after) =>
      selectedTrainer
        ? listAssignedMembers(selectedTrainer.id, {
            limit: PAGE,
            ...(after ? { after } : {}),
          })
        : Promise.resolve({
            data: [],
            meta: { has_more: false, next_after: null },
          }),
  );
  const trainerChoices = useLookupOptions<TrainerIdentity>(
    "assignment-trainers",
    (after) => listTrainers({ limit: "100", ...(after ? { after } : {}) }),
    mode === "create",
  );

  useEffect(() => {
    if (mode !== "create") return;
    createDialogRef.current?.focus({ preventScroll: true });
  }, [mode]);

  function showAssigned(trainer: TrainerIdentity) {
    setSelectedTrainer(trainer);
    setTrainerId(trainer.id);
  }

  async function run(
    work: () => Promise<unknown>,
    success: string,
  ): Promise<void> {
    if (pendingRef.current) return;
    pendingRef.current = true;
    setPending(true);
    setCommandError("");
    setNotice("");
    try {
      await work();
      setNotice(success);
      setMode(null);
      setActive(null);
      assignments.reload();
      assigned.reload();
    } catch (caught) {
      setCommandError(phase5Error(caught, "The assignment command failed."));
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  }

  function closeCreateAssignment() {
    if (pendingRef.current) return;
    setCommandError("");
    setMode(null);
  }

  return (
    <div className="grid gap-6">
      <Alert message={mode === "create" ? "" : commandError} />
      {notice ? (
        <p
          aria-live="polite"
          className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-800"
          role="status"
        >
          {notice}
        </p>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[minmax(18rem,0.8fr)_minmax(22rem,1.2fr)]">
        <section
          aria-labelledby="trainer-directory-title"
          className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
        >
          <div className="border-b border-slate-100 px-5 py-4">
            <p className="text-xs font-bold uppercase tracking-[0.16em] text-emerald-700">
              Team
            </p>
            <h2
              className="mt-1 text-xl font-bold text-slate-900"
              id="trainer-directory-title"
            >
              Trainer directory
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              Select a trainer to view today&apos;s members.
            </p>
          </div>
          <div className="grid gap-3 p-4">
            {trainers.pending ? <Loading label="Loading trainers." /> : null}
            <Alert message={trainers.error} />
            {!trainers.pending && trainers.rows.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-6 text-center">
                <Empty label="No trainers are visible." />
              </div>
            ) : (
              <ul className="grid gap-2">
                {trainers.rows.map((trainer) => {
                  const selected = selectedTrainer?.id === trainer.id;
                  return (
                    <li key={trainer.id}>
                      <button
                        aria-label={`${trainer.display_name} (${trainer.staff_code})`}
                        aria-pressed={selected}
                        className={`group flex w-full items-center gap-3 rounded-xl border p-3 text-left transition ${
                          selected
                            ? "border-emerald-300 bg-emerald-50 shadow-sm"
                            : "border-transparent bg-slate-50 hover:border-slate-200 hover:bg-white hover:shadow-sm"
                        }`}
                        onClick={() => showAssigned(trainer)}
                        type="button"
                      >
                        <span
                          aria-hidden="true"
                          className={`grid size-11 shrink-0 place-items-center rounded-xl text-sm font-black ${
                            selected
                              ? "bg-emerald-600 text-white"
                              : "bg-slate-200 text-slate-700 group-hover:bg-emerald-100 group-hover:text-emerald-800"
                          }`}
                        >
                          {initials(trainer.display_name)}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate font-semibold text-slate-900">
                            {trainer.display_name}
                          </span>
                          <span className="mt-0.5 block text-xs font-medium text-slate-500">
                            {trainer.staff_code}
                          </span>
                        </span>
                        <span aria-hidden="true" className="text-slate-400">
                          →
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
            <CursorControls
              bounded={trainers.bounded}
              hasMore={trainers.hasMore}
              index={trainers.index}
              onNext={() => void trainers.goNext()}
              onPrevious={() => void trainers.goPrevious()}
              pending={trainers.pending}
            />
          </div>
        </section>

        <section
          aria-labelledby="assigned-members-title"
          className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
        >
          <div className="border-b border-slate-100 px-5 py-4">
            <p className="text-xs font-bold uppercase tracking-[0.16em] text-blue-700">
              Today
            </p>
            <h2
              className="mt-1 text-xl font-bold text-slate-900"
              id="assigned-members-title"
            >
              Assigned members
            </h2>
            {selectedTrainer ? (
              <p className="mt-1 text-sm text-slate-500">
                Members assigned to{" "}
                <span className="font-semibold text-slate-700">
                  {selectedTrainer.display_name}
                </span>
              </p>
            ) : null}
          </div>
          <div className="grid gap-3 p-4">
            {!selectedTrainer ? (
              <div className="grid min-h-48 place-items-center rounded-xl border border-dashed border-slate-300 bg-slate-50 p-6 text-center">
                <div>
                  <div
                    aria-hidden="true"
                    className="mx-auto mb-3 grid size-12 place-items-center rounded-full bg-white text-xl shadow-sm"
                  >
                    ↖
                  </div>
                  <Empty label="Choose a trainer to see currently assigned members." />
                </div>
              </div>
            ) : null}
            {selectedTrainer && assigned.pending ? (
              <Loading label="Loading assigned members." />
            ) : null}
            <Alert message={assigned.error} />
            {selectedTrainer &&
            !assigned.pending &&
            assigned.rows.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-6 text-center">
                <Empty label="No members are assigned today." />
              </div>
            ) : (
              <ul className="grid gap-2">
                {assigned.rows.map((row) => (
                  <li
                    className="flex items-center gap-3 rounded-xl border border-slate-200 p-3"
                    key={row.assignment_id}
                  >
                    <span
                      aria-hidden="true"
                      className="grid size-10 shrink-0 place-items-center rounded-full bg-blue-50 text-sm font-bold text-blue-700"
                    >
                      {initials(memberLabel(row))}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-semibold text-slate-900">
                        {memberLabel(row)}
                      </span>
                      <span className="block text-xs text-slate-500">
                        {row.member_number} · From {row.effective_from}
                        {row.effective_until
                          ? ` to ${row.effective_until}`
                          : ""}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {selectedTrainer ? (
              <CursorControls
                bounded={assigned.bounded}
                hasMore={assigned.hasMore}
                index={assigned.index}
                onNext={() => void assigned.goNext()}
                onPrevious={() => void assigned.goPrevious()}
                pending={assigned.pending}
              />
            ) : null}
          </div>
        </section>
      </div>

      <section
        aria-labelledby="assignment-list-title"
        className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
      >
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.16em] text-amber-700">
              History
            </p>
            <h2
              className="mt-1 text-xl font-bold text-slate-900"
              id="assignment-list-title"
            >
              Assignments
            </h2>
          </div>
          {canCreate ? (
            <button
              aria-controls={
                mode === "create" ? "create-assignment-dialog" : undefined
              }
              aria-expanded={mode === "create"}
              className="button button-create"
              onClick={() => {
                setMode("create");
                setCommandError("");
              }}
              type="button"
            >
              <span aria-hidden="true">+</span>
              Create assignment
            </button>
          ) : null}
        </div>
        <div className="grid gap-5 p-4 sm:p-5">
          <div className="grid items-end gap-3 rounded-xl bg-slate-50 p-4 md:grid-cols-[minmax(0,1fr)_14rem_auto]">
            <MemberSearch
              action="Search assignment members"
              label="Filter assignments by member"
              onSelect={setMemberFilter}
              selected={memberFilter}
            />
            <Field label="Assignment status">
              <select
                className="input"
                onChange={(event) => setStatus(event.target.value)}
                value={status}
              >
                <option value="">All statuses</option>
                <option value="ACTIVE">ACTIVE</option>
                <option value="ENDED">ENDED</option>
                <option value="CANCELLED">CANCELLED</option>
              </select>
            </Field>
            {memberFilter ? (
              <button
                className="min-h-11 text-sm font-semibold text-slate-600 underline"
                onClick={() => setMemberFilter(null)}
                type="button"
              >
                Clear member filter
              </button>
            ) : (
              <span />
            )}
          </div>
          {assignments.pending ? (
            <Loading label="Loading assignments." />
          ) : null}
          <Alert message={assignments.error} />
          {!assignments.pending && assignments.rows.length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-8 text-center">
              <Empty label="No assignments match." />
            </div>
          ) : (
            <ul className="grid gap-3">
              {assignments.rows.map((row) => (
                <li
                  className="rounded-xl border border-slate-200 p-4 transition hover:border-slate-300 hover:shadow-sm"
                  key={row.id}
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-3">
                      <span
                        aria-hidden="true"
                        className="grid size-10 shrink-0 place-items-center rounded-xl bg-slate-100 text-sm font-black text-slate-700"
                      >
                        {initials(row.trainer.display_name)}
                      </span>
                      <div>
                        <p className="font-semibold text-slate-900">
                          {row.trainer.display_name}
                          <span className="mx-2 text-slate-300">→</span>
                          {memberLabel(row.member)}
                        </p>
                        <p className="mt-1 text-sm text-slate-500">
                          {row.effective_from}
                          {row.effective_until
                            ? ` to ${row.effective_until}`
                            : " onward"}
                          {row.reason ? ` · ${row.reason}` : ""}
                        </p>
                      </div>
                    </div>
                    <span
                      className={`rounded-full px-2.5 py-1 text-xs font-bold ring-1 ring-inset ${statusClass(row.status)}`}
                    >
                      {row.status}
                    </span>
                  </div>
                  {canClose && row.status === "ACTIVE" ? (
                    <div className="mt-4 flex flex-wrap gap-2 border-t border-slate-100 pt-3">
                      <button
                        className="rounded-lg px-3 py-2 text-sm font-semibold text-amber-700 hover:bg-amber-50"
                        onClick={() => {
                          setActive(row);
                          setMode("end");
                          setCommandError("");
                        }}
                        type="button"
                      >
                        End assignment
                      </button>
                      <button
                        className="rounded-lg px-3 py-2 text-sm font-semibold text-rose-700 hover:bg-rose-50"
                        onClick={() => {
                          setActive(row);
                          setMode("cancel");
                          setCommandError("");
                        }}
                        type="button"
                      >
                        Cancel assignment
                      </button>
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
          <CursorControls
            bounded={assignments.bounded}
            hasMore={assignments.hasMore}
            index={assignments.index}
            onNext={() => void assignments.goNext()}
            onPrevious={() => void assignments.goPrevious()}
            pending={assignments.pending}
          />
        </div>
      </section>

      {mode === "create" && canCreate ? (
        <div
          className="fixed inset-0 z-50 grid overflow-hidden p-2 sm:place-items-center sm:p-6"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeCreateAssignment();
          }}
        >
          <form
            aria-busy={pending}
            aria-labelledby="create-assignment-title"
            aria-modal="true"
            className="flex h-full max-h-full min-h-0 w-full flex-col overflow-y-auto overscroll-contain sm:h-auto sm:max-h-[min(90dvh,48rem)] sm:max-w-xl"
            id="create-assignment-dialog"
            onKeyDown={(event) => {
              if (event.key !== "Escape") return;
              event.stopPropagation();
              if (pendingRef.current) return;
              event.preventDefault();
              closeCreateAssignment();
            }}
            onSubmit={(event) => {
              event.preventDefault();
              if (pendingRef.current) return;
              const form = new FormData(event.currentTarget);
              const trainer = String(
                form.get("trainer_staff_profile_id") ?? "",
              );
              const until = String(form.get("effective_until") ?? "");
              if (!member) {
                setCommandError("Select an active member.");
                return;
              }
              void run(
                () =>
                  createAssignment({
                    trainer_staff_profile_id: trainer,
                    member_id: member.id,
                    effective_from: String(form.get("effective_from") ?? ""),
                    effective_until: until || null,
                    reason: String(form.get("reason") ?? ""),
                  }),
                "Assignment created.",
              );
            }}
            ref={createDialogRef}
            role="dialog"
            tabIndex={-1}
          >
            <div className="sticky top-0 z-10 flex items-start gap-3 border-b border-slate-100 bg-white px-5 py-4 sm:px-6">
              <span
                aria-hidden="true"
                className="grid min-h-9 shrink-0 place-items-center rounded-lg bg-blue-100 px-2 text-[0.65rem] font-black tracking-wide text-blue-800"
              >
                PT
              </span>
              <div className="min-w-0 flex-1">
                <h2
                  className="text-xl font-bold text-slate-900"
                  id="create-assignment-title"
                >
                  Create assignment
                </h2>
                <p className="mt-1 text-sm text-slate-500">
                  Choose a trainer, an active member, and the dates this
                  assignment covers.
                </p>
              </div>
              <button
                className="button button-cancel button-sm"
                disabled={pending}
                onClick={closeCreateAssignment}
                type="button"
              >
                Close
              </button>
            </div>
            <div className="grid gap-4 px-5 py-4 sm:px-6">
              <Alert message={trainerChoices.error} />
              <Field label="Trainer">
                <select
                  className="input"
                  name="trainer_staff_profile_id"
                  required
                >
                  <option value="">Select a trainer</option>
                  {trainerChoices.items.map((trainer) => (
                    <option key={trainer.id} value={trainer.id}>
                      {trainer.display_name} ({trainer.staff_code})
                    </option>
                  ))}
                </select>
              </Field>
              <MemberSearch
                disabled={pending}
                onSelect={setMember}
                selected={member}
              />
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Effective from">
                  <TextInput
                    defaultValue={today}
                    name="effective_from"
                    required
                    type="date"
                  />
                </Field>
                <Field label="Effective until">
                  <TextInput name="effective_until" type="date" />
                </Field>
              </div>
              <Field label="Reason">
                <TextInput name="reason" />
              </Field>
            </div>
            <div className="sticky bottom-0 z-10 mt-auto border-t border-slate-100 bg-white px-5 py-4 sm:px-6">
              {commandError ? (
                <div className="mb-3">
                  <Alert message={commandError} />
                </div>
              ) : null}
              <div className="flex flex-col sm:flex-row">
                <button
                  className="button button-cancel w-full sm:w-auto"
                  disabled={pending}
                  onClick={closeCreateAssignment}
                  type="button"
                >
                  Cancel
                </button>
                <button
                  className="button button-edit w-full sm:w-auto"
                  disabled={pending}
                  type="submit"
                >
                  Save assignment
                </button>
              </div>
            </div>
          </form>
        </div>
      ) : null}

      {mode === "end" && active && canClose ? (
        <form
          aria-labelledby="end-assignment-title"
          className="grid gap-4 rounded-2xl border border-amber-200 border-t-4 border-t-amber-500 bg-white p-5 shadow-sm sm:p-6"
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            void run(
              () =>
                endAssignment(active.id, {
                  effective_until: String(form.get("effective_until") ?? ""),
                  reason: String(form.get("reason") ?? ""),
                }),
              "Assignment ended.",
            );
          }}
          role="dialog"
        >
          <h3 className="font-semibold" id="end-assignment-title">
            End assignment
          </h3>
          <Field label="Effective until">
            <TextInput
              defaultValue={today}
              name="effective_until"
              required
              type="date"
            />
          </Field>
          <ReasonField label="Reason for ending" />
          <button
            className="button button-warn"
            disabled={pending}
            type="submit"
          >
            Confirm end
          </button>
        </form>
      ) : null}

      {mode === "cancel" && active && canClose ? (
        <form
          aria-labelledby="cancel-assignment-title"
          className="grid gap-4 rounded-2xl border border-rose-200 border-t-4 border-t-rose-500 bg-white p-5 shadow-sm sm:p-6"
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            void run(
              () =>
                cancelAssignment(active.id, {
                  reason: String(form.get("reason") ?? ""),
                }),
              "Assignment cancelled.",
            );
          }}
          role="dialog"
        >
          <h3 className="font-semibold" id="cancel-assignment-title">
            Cancel assignment
          </h3>
          <ReasonField label="Reason for cancelling" />
          <button
            className="button button-cancel"
            disabled={pending}
            type="submit"
          >
            Confirm cancel
          </button>
        </form>
      ) : null}
    </div>
  );
}
