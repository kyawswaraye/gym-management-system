"use client";

import { useState } from "react";

import {
  Alert,
  CursorControls,
  Empty,
  Field,
  Loading,
  ReasonField,
  StatusText,
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

  function showAssigned(trainer: TrainerIdentity) {
    setSelectedTrainer(trainer);
    setTrainerId(trainer.id);
  }

  async function run(
    work: () => Promise<unknown>,
    success: string,
  ): Promise<void> {
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
      setPending(false);
    }
  }

  return (
    <div className="grid gap-6">
      <Alert message={commandError} />
      {notice ? (
        <p aria-live="polite" role="status">
          {notice}
        </p>
      ) : null}

      <section aria-labelledby="trainer-directory-title" className="grid gap-3">
        <h2 className="text-xl font-semibold" id="trainer-directory-title">
          Trainer directory
        </h2>
        {trainers.pending ? <Loading label="Loading trainers." /> : null}
        <Alert message={trainers.error} />
        {!trainers.pending && trainers.rows.length === 0 ? (
          <Empty label="No trainers are visible." />
        ) : (
          <ul className="grid gap-2">
            {trainers.rows.map((trainer) => (
              <li key={trainer.id}>
                <button
                  className="underline"
                  onClick={() => showAssigned(trainer)}
                  type="button"
                >
                  {trainer.display_name} ({trainer.staff_code})
                </button>
              </li>
            ))}
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
      </section>

      <section aria-labelledby="assigned-members-title" className="grid gap-3">
        <h2 className="text-xl font-semibold" id="assigned-members-title">
          Assigned members
        </h2>
        {selectedTrainer ? (
          <p>
            Showing members assigned to {selectedTrainer.display_name} today.
          </p>
        ) : (
          <Empty label="Choose a trainer to see currently assigned members." />
        )}
        {selectedTrainer && assigned.pending ? (
          <Loading label="Loading assigned members." />
        ) : null}
        <Alert message={assigned.error} />
        {selectedTrainer && !assigned.pending && assigned.rows.length === 0 ? (
          <Empty label="No members are assigned today." />
        ) : (
          <ul className="grid gap-2">
            {assigned.rows.map((row) => (
              <li key={row.assignment_id}>
                {memberLabel(row)} · {row.member_number} · {row.effective_from}
                {row.effective_until ? ` to ${row.effective_until}` : " onward"}
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
      </section>

      <section aria-labelledby="assignment-list-title" className="grid gap-3">
        <h2 className="text-xl font-semibold" id="assignment-list-title">
          Assignments
        </h2>
        <MemberSearch
          action="Search assignment members"
          label="Filter assignments by member"
          onSelect={setMemberFilter}
          selected={memberFilter}
        />
        {memberFilter ? (
          <button
            className="underline"
            onClick={() => setMemberFilter(null)}
            type="button"
          >
            Clear member filter
          </button>
        ) : null}
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
        {assignments.pending ? <Loading label="Loading assignments." /> : null}
        <Alert message={assignments.error} />
        {!assignments.pending && assignments.rows.length === 0 ? (
          <Empty label="No assignments match." />
        ) : (
          <ul className="grid gap-3">
            {assignments.rows.map((row) => (
              <li className="rounded border border-slate-200 p-3" key={row.id}>
                <p>
                  {row.trainer.display_name} · {memberLabel(row.member)}{" "}
                  <StatusText status={row.status} />
                </p>
                <p className="muted">
                  {row.effective_from}
                  {row.effective_until
                    ? ` to ${row.effective_until}`
                    : " onward"}
                  {row.reason ? ` · ${row.reason}` : ""}
                </p>
                {canClose && row.status === "ACTIVE" ? (
                  <div className="mt-2 flex gap-3">
                    <button
                      className="underline"
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
                      className="underline"
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
        {canCreate ? (
          <button
            className="button"
            onClick={() => {
              setMode("create");
              setCommandError("");
            }}
            type="button"
          >
            Create assignment
          </button>
        ) : null}
      </section>

      {mode === "create" && canCreate ? (
        <form
          aria-labelledby="create-assignment-title"
          className="grid gap-3 rounded border border-slate-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            const trainer = String(form.get("trainer_staff_profile_id") ?? "");
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
          role="dialog"
        >
          <h3 className="font-semibold" id="create-assignment-title">
            Create assignment
          </h3>
          <Alert message={trainerChoices.error} />
          <Field label="Trainer">
            <select className="input" name="trainer_staff_profile_id" required>
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
          <Field label="Reason">
            <TextInput name="reason" />
          </Field>
          <button className="button" disabled={pending} type="submit">
            Save assignment
          </button>
        </form>
      ) : null}

      {mode === "end" && active && canClose ? (
        <form
          aria-labelledby="end-assignment-title"
          className="grid gap-3 rounded border border-slate-200 p-4"
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
          <button className="button" disabled={pending} type="submit">
            Confirm end
          </button>
        </form>
      ) : null}

      {mode === "cancel" && active && canClose ? (
        <form
          aria-labelledby="cancel-assignment-title"
          className="grid gap-3 rounded border border-slate-200 p-4"
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
          <button className="button" disabled={pending} type="submit">
            Confirm cancel
          </button>
        </form>
      ) : null}
    </div>
  );
}
