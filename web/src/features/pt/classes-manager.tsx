"use client";

import { useRef, useState } from "react";

import {
  Alert,
  CursorControls,
  Empty,
  Field,
  Loading,
  ReasonField,
  SelectInput,
  StatusText,
  TextInput,
} from "@/features/finance/ui";
import { useCursorPages } from "@/features/finance/paging";
import {
  attendClassBooking,
  cancelClassBooking,
  cancelClassOccurrence,
  createClassBooking,
  createClassSchedule,
  createClassType,
  listClassBookings,
  listClassOccurrences,
  listClassSchedules,
  listClassTypes,
  listMemberPackages,
  listTrainers,
  noShowClassBooking,
  updateClassSchedule,
  updateClassType,
} from "@/features/pt/client";
import { phase5Error } from "@/features/pt/errors";
import { collectLookupPages, useLookupOptions } from "@/features/pt/lookup";
import { MemberSearch, type PickedMember } from "@/features/pt/member-search";
import type {
  ClassBooking,
  ClassOccurrence,
  ClassSchedule,
  ClassType,
  MemberServicePackage,
  TrainerIdentity,
} from "@/types/api";

const PAGE = "25";
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function shiftDate(iso: string, days: number): string {
  const [year, month, day] = iso.split("-").map(Number);
  const date = new Date(Date.UTC(year ?? 0, (month ?? 1) - 1, day ?? 1));
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

export function ClassesManager({
  canManage,
  canBook,
  canAttend,
  canUnmetered,
  canLateCancel,
  today,
}: {
  canManage: boolean;
  canBook: boolean;
  canAttend: boolean;
  canUnmetered: boolean;
  canLateCancel: boolean;
  today: string;
}) {
  const [dateFrom, setDateFrom] = useState(today);
  const [dateTo, setDateTo] = useState(shiftDate(today, 6));
  const [bookingMember, setBookingMember] = useState<PickedMember | null>(null);
  const [member, setMember] = useState<PickedMember | null>(null);
  const [packages, setPackages] = useState<MemberServicePackage[]>([]);
  const [packageNotice, setPackageNotice] = useState("");
  const [unmetered, setUnmetered] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [editingType, setEditingType] = useState<ClassType | null>(null);
  const [editing, setEditing] = useState<ClassSchedule | null>(null);
  const [cancellingOccurrence, setCancellingOccurrence] =
    useState<ClassOccurrence | null>(null);
  const [cancellingBooking, setCancellingBooking] =
    useState<ClassBooking | null>(null);
  const key = useRef<{ fingerprint: string; value: string } | null>(null);
  const types = useCursorPages<ClassType>("class-types", (after) =>
    listClassTypes({ limit: PAGE, ...(after ? { after } : {}) }),
  );
  const schedules = useCursorPages<ClassSchedule>("class-schedules", (after) =>
    listClassSchedules({ limit: PAGE, ...(after ? { after } : {}) }),
  );
  const occurrences = useCursorPages<ClassOccurrence>(
    `occurrences:${dateFrom}:${dateTo}`,
    (after) =>
      listClassOccurrences({
        limit: PAGE,
        view: "calendar",
        date_from: dateFrom,
        date_to: dateTo,
        ...(after ? { after } : {}),
      }),
  );
  const bookings = useCursorPages<ClassBooking>(
    `class-bookings:${dateFrom}:${dateTo}:${bookingMember?.id ?? ""}`,
    (after) =>
      listClassBookings({
        limit: PAGE,
        view: "calendar",
        date_from: dateFrom,
        date_to: dateTo,
        ...(after ? { after } : {}),
        ...(bookingMember ? { member_id: bookingMember.id } : {}),
      }),
  );
  const trainers = useLookupOptions<TrainerIdentity>(
    "class-trainers",
    (after) => listTrainers({ limit: "100", ...(after ? { after } : {}) }),
    canManage,
  );
  const activeTypes = useLookupOptions<ClassType>(
    "active-class-types",
    (after) =>
      listClassTypes({
        limit: "100",
        is_active: "true",
        ...(after ? { after } : {}),
      }),
    canManage,
  );

  function commandKey(fingerprint: string): string {
    if (!key.current || key.current.fingerprint !== fingerprint) {
      key.current = { fingerprint, value: crypto.randomUUID() };
    }
    return key.current.value;
  }

  async function command(
    work: () => Promise<unknown>,
    success: string,
  ): Promise<boolean> {
    setPending(true);
    setError("");
    setNotice("");
    try {
      await work();
      key.current = null;
      setNotice(success);
      types.reload();
      schedules.reload();
      occurrences.reload();
      bookings.reload();
      activeTypes.reload();
      return true;
    } catch (caught) {
      setError(phase5Error(caught, "The class command failed."));
      return false;
    } finally {
      setPending(false);
    }
  }

  function clock(value: string): string {
    return value.slice(0, 5);
  }

  const canCancelBooking = canBook || canManage;

  return (
    <div className="grid gap-8">
      <Alert message={error} />
      {notice ? (
        <p aria-live="polite" role="status">
          {notice}
        </p>
      ) : null}
      <section aria-labelledby="class-types-title" className="grid gap-3">
        <h2 className="text-xl font-semibold" id="class-types-title">
          Class types
        </h2>
        {types.pending ? <Loading label="Loading class types." /> : null}
        <Alert message={types.error} />
        {!types.pending && types.rows.length === 0 ? (
          <Empty label="No class types exist." />
        ) : (
          <ul className="grid gap-2">
            {types.rows.map((row) => (
              <li key={row.id}>
                {row.code} · {row.name} · {row.default_duration_minutes} min ·
                capacity {row.default_capacity} ·{" "}
                {row.is_active ? "active" : "archived"}
                {row.description ? ` · ${row.description}` : ""}
                {canManage ? (
                  <button
                    className="ml-3 underline"
                    disabled={pending}
                    onClick={() => setEditingType(row)}
                    type="button"
                  >
                    Edit class type
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        <CursorControls
          bounded={types.bounded}
          hasMore={types.hasMore}
          index={types.index}
          onNext={() => void types.goNext()}
          onPrevious={() => void types.goPrevious()}
          pending={types.pending}
        />
        {canManage ? (
          <form
            className="grid gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              void command(
                () =>
                  createClassType({
                    code: String(form.get("code") ?? ""),
                    name: String(form.get("name") ?? ""),
                    description: String(form.get("description") ?? ""),
                    default_duration_minutes: Number(
                      form.get("default_duration_minutes"),
                    ),
                    default_capacity: Number(form.get("default_capacity")),
                  }),
                "Class type created.",
              );
            }}
          >
            <Field label="Code">
              <TextInput name="code" required />
            </Field>
            <Field label="Name">
              <TextInput name="name" required />
            </Field>
            <Field label="Description">
              <TextInput name="description" />
            </Field>
            <Field label="Default duration minutes">
              <TextInput
                min={1}
                name="default_duration_minutes"
                required
                type="number"
              />
            </Field>
            <Field label="Default capacity">
              <TextInput
                min={1}
                name="default_capacity"
                required
                type="number"
              />
            </Field>
            <button className="button" disabled={pending} type="submit">
              Create class type
            </button>
          </form>
        ) : null}
        {editingType && canManage ? (
          <form
            aria-labelledby="edit-class-type-title"
            className="grid gap-3 rounded border border-slate-200 p-4"
            key={editingType.id}
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              const body = {
                name: String(form.get("name") ?? ""),
                description: String(form.get("description") ?? ""),
                default_duration_minutes: Number(
                  form.get("default_duration_minutes"),
                ),
                default_capacity: Number(form.get("default_capacity")),
                is_active: form.get("is_active") === "true",
              };
              void command(
                () => updateClassType(editingType.id, body),
                "Class type updated.",
              ).then((saved) => {
                if (saved) setEditingType(null);
              });
            }}
            role="dialog"
          >
            <h3 className="font-semibold" id="edit-class-type-title">
              Edit class type
            </h3>
            <p className="muted">Code {editingType.code} stays unchanged.</p>
            <Field label="Name">
              <TextInput defaultValue={editingType.name} name="name" required />
            </Field>
            <Field label="Description">
              <TextInput
                defaultValue={editingType.description}
                name="description"
              />
            </Field>
            <Field label="Default duration minutes">
              <TextInput
                defaultValue={editingType.default_duration_minutes}
                min={1}
                name="default_duration_minutes"
                required
                type="number"
              />
            </Field>
            <Field label="Default capacity">
              <TextInput
                defaultValue={editingType.default_capacity}
                min={1}
                name="default_capacity"
                required
                type="number"
              />
            </Field>
            <Field label="Status">
              <SelectInput
                defaultValue={editingType.is_active ? "true" : "false"}
                name="is_active"
              >
                <option value="true">Active</option>
                <option value="false">Archived</option>
              </SelectInput>
            </Field>
            <button className="button" disabled={pending} type="submit">
              Save class type
            </button>
          </form>
        ) : null}
      </section>

      <section aria-labelledby="schedules-title" className="grid gap-3">
        <h2 className="text-xl font-semibold" id="schedules-title">
          Weekly schedules
        </h2>
        {schedules.pending ? <Loading label="Loading schedules." /> : null}
        <Alert message={schedules.error} />
        {!schedules.pending && schedules.rows.length === 0 ? (
          <Empty label="No schedules match." />
        ) : (
          <ul className="grid gap-2">
            {schedules.rows.map((row) => (
              <li key={row.id}>
                Type {row.class_type_id} · trainer{" "}
                {row.trainer_staff_profile_id} · {row.location}
                {row.location_is_exclusive ? " exclusive" : ""} ·{" "}
                {row.local_start_time}–{row.local_end_time} · weekdays{" "}
                {row.weekdays.join(",")} · capacity {row.capacity} ·{" "}
                {row.effective_from} to {row.effective_until}{" "}
                <StatusText status={row.status} />
                {canManage && row.status === "ACTIVE" ? (
                  <button
                    className="ml-3 underline"
                    onClick={() => setEditing(row)}
                    type="button"
                  >
                    Update schedule
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        <CursorControls
          bounded={schedules.bounded}
          hasMore={schedules.hasMore}
          index={schedules.index}
          onNext={() => void schedules.goNext()}
          onPrevious={() => void schedules.goPrevious()}
          pending={schedules.pending}
        />
        {canManage ? (
          <form
            className="grid gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              const weekdays = WEEKDAYS.map((_, index) => index).filter(
                (index) => form.get(`weekday_${index}`),
              );
              const body = {
                class_type_id: String(form.get("class_type_id") ?? ""),
                trainer_staff_profile_id: String(
                  form.get("trainer_staff_profile_id") ?? "",
                ),
                weekdays,
                local_start_time: String(form.get("local_start_time") ?? ""),
                local_end_time: String(form.get("local_end_time") ?? ""),
                effective_from: String(form.get("effective_from") ?? ""),
                effective_until: String(form.get("effective_until") ?? ""),
                location: String(form.get("location") ?? ""),
                location_is_exclusive:
                  form.get("location_is_exclusive") === "on",
                capacity: Number(form.get("capacity")),
              };
              void command(
                () =>
                  createClassSchedule(body, commandKey(JSON.stringify(body))),
                "Schedule created.",
              );
            }}
          >
            <Alert message={activeTypes.error || trainers.error} />
            <Field label="Class type">
              <SelectInput name="class_type_id" required>
                <option value="">Select a type</option>
                {activeTypes.items.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.code} · {row.name}
                  </option>
                ))}
              </SelectInput>
            </Field>
            <Field label="Trainer">
              <SelectInput name="trainer_staff_profile_id" required>
                <option value="">Select a trainer</option>
                {trainers.items.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.display_name} ({row.staff_code})
                  </option>
                ))}
              </SelectInput>
            </Field>
            <fieldset>
              <legend>Weekdays</legend>
              {WEEKDAYS.map((label, index) => (
                <label className="mr-3" key={label}>
                  <input name={`weekday_${index}`} type="checkbox" /> {label}
                </label>
              ))}
            </fieldset>
            <Field label="Start time">
              <TextInput name="local_start_time" required type="time" />
            </Field>
            <Field label="End time">
              <TextInput name="local_end_time" required type="time" />
            </Field>
            <Field label="Effective from">
              <TextInput
                defaultValue={today}
                name="effective_from"
                required
                type="date"
              />
            </Field>
            <Field label="Effective until">
              <TextInput name="effective_until" required type="date" />
            </Field>
            <Field label="Location">
              <TextInput name="location" required />
            </Field>
            <label>
              <input name="location_is_exclusive" type="checkbox" /> Exclusive
              location
            </label>
            <Field label="Capacity">
              <TextInput min={1} name="capacity" required type="number" />
            </Field>
            <button className="button" disabled={pending} type="submit">
              Create schedule
            </button>
          </form>
        ) : null}
        {editing && canManage ? (
          <form
            aria-labelledby="edit-schedule-title"
            className="grid gap-3 rounded border border-slate-200 p-4"
            key={editing.id}
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              const weekdays = WEEKDAYS.map((_, index) => index).filter(
                (index) => form.get(`weekday_${index}`),
              );
              if (weekdays.length === 0) {
                setError("Choose at least one weekday.");
                return;
              }
              const body = {
                class_type_id: String(form.get("class_type_id") ?? ""),
                trainer_staff_profile_id: String(
                  form.get("trainer_staff_profile_id") ?? "",
                ),
                weekdays,
                local_start_time: String(form.get("local_start_time") ?? ""),
                local_end_time: String(form.get("local_end_time") ?? ""),
                effective_from: String(form.get("effective_from") ?? ""),
                effective_until: String(form.get("effective_until") ?? ""),
                location: String(form.get("location") ?? ""),
                location_is_exclusive:
                  form.get("location_is_exclusive") === "on",
                capacity: Number(form.get("capacity")),
              };
              if (body.local_end_time <= body.local_start_time) {
                setError("End time must be after the start time.");
                return;
              }
              if (body.effective_until < body.effective_from) {
                setError("The end date is before the start date.");
                return;
              }
              void command(
                () =>
                  updateClassSchedule(
                    editing.id,
                    body,
                    commandKey(JSON.stringify({ id: editing.id, ...body })),
                  ),
                "Schedule updated.",
              ).then((saved) => {
                if (saved) setEditing(null);
              });
            }}
            role="dialog"
          >
            <h3 className="font-semibold" id="edit-schedule-title">
              Update schedule
            </h3>
            <p className="muted">
              Saving regenerates future occurrences that are still unbooked.
              Booked, attended, and no-show occurrences stay in place.
            </p>
            <Alert message={activeTypes.error || trainers.error} />
            <Field label="Class type">
              <SelectInput
                defaultValue={editing.class_type_id}
                name="class_type_id"
                required
              >
                <option value="">Select a type</option>
                {activeTypes.items.some(
                  (row) => row.id === editing.class_type_id,
                ) ? null : (
                  <option value={editing.class_type_id}>
                    Current class type
                  </option>
                )}
                {activeTypes.items.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.code} · {row.name}
                  </option>
                ))}
              </SelectInput>
            </Field>
            <Field label="Trainer">
              <SelectInput
                defaultValue={editing.trainer_staff_profile_id}
                name="trainer_staff_profile_id"
                required
              >
                <option value="">Select a trainer</option>
                {trainers.items.some(
                  (row) => row.id === editing.trainer_staff_profile_id,
                ) ? null : (
                  <option value={editing.trainer_staff_profile_id}>
                    Current trainer
                  </option>
                )}
                {trainers.items.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.display_name} ({row.staff_code})
                  </option>
                ))}
              </SelectInput>
            </Field>
            <fieldset>
              <legend>Weekdays</legend>
              {WEEKDAYS.map((label, index) => (
                <label className="mr-3" key={label}>
                  <input
                    defaultChecked={editing.weekdays.includes(index)}
                    name={`weekday_${index}`}
                    type="checkbox"
                  />{" "}
                  {label}
                </label>
              ))}
            </fieldset>
            <Field label="Start time">
              <TextInput
                defaultValue={clock(editing.local_start_time)}
                name="local_start_time"
                required
                type="time"
              />
            </Field>
            <Field label="End time">
              <TextInput
                defaultValue={clock(editing.local_end_time)}
                name="local_end_time"
                required
                type="time"
              />
            </Field>
            <Field label="Effective from">
              <TextInput
                defaultValue={editing.effective_from}
                name="effective_from"
                required
                type="date"
              />
            </Field>
            <Field label="Effective until">
              <TextInput
                defaultValue={editing.effective_until}
                name="effective_until"
                required
                type="date"
              />
            </Field>
            <Field label="Location">
              <TextInput
                defaultValue={editing.location}
                name="location"
                required
              />
            </Field>
            <label>
              <input
                defaultChecked={editing.location_is_exclusive}
                name="location_is_exclusive"
                type="checkbox"
              />{" "}
              Exclusive location
            </label>
            <Field label="Capacity">
              <TextInput
                defaultValue={editing.capacity}
                min={1}
                name="capacity"
                required
                type="number"
              />
            </Field>
            <button className="button" disabled={pending} type="submit">
              Save schedule
            </button>
          </form>
        ) : null}
      </section>

      <section aria-labelledby="occurrences-title" className="grid gap-3">
        <h2 className="text-xl font-semibold" id="occurrences-title">
          Occurrences
        </h2>
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            setDateFrom(String(form.get("date_from") ?? today));
            setDateTo(String(form.get("date_to") ?? today));
          }}
        >
          <Field label="From">
            <TextInput
              defaultValue={dateFrom}
              name="date_from"
              required
              type="date"
            />
          </Field>
          <Field label="To">
            <TextInput
              defaultValue={dateTo}
              name="date_to"
              required
              type="date"
            />
          </Field>
          <button className="button" type="submit">
            Show occurrences
          </button>
        </form>
        {occurrences.pending ? <Loading label="Loading occurrences." /> : null}
        <Alert message={occurrences.error} />
        {!occurrences.pending && occurrences.rows.length === 0 ? (
          <Empty label="No occurrences are in this range." />
        ) : (
          <ul className="grid gap-2">
            {occurrences.rows.map((row) => (
              <li key={row.id}>
                {row.local_date} · {row.starts_at}–{row.ends_at} · type{" "}
                {row.class_type_id} · trainer {row.trainer_staff_profile_id} ·{" "}
                {row.location}
                {row.location_is_exclusive ? " exclusive" : ""} · capacity{" "}
                {row.capacity} <StatusText status={row.status} />
                {canManage && row.status === "SCHEDULED" ? (
                  <button
                    className="ml-3 underline"
                    disabled={pending}
                    onClick={() => setCancellingOccurrence(row)}
                    type="button"
                  >
                    Cancel occurrence
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        <CursorControls
          bounded={occurrences.bounded}
          hasMore={occurrences.hasMore}
          index={occurrences.index}
          onNext={() => void occurrences.goNext()}
          onPrevious={() => void occurrences.goPrevious()}
          pending={occurrences.pending}
        />
        {cancellingOccurrence && canManage ? (
          <form
            aria-labelledby="cancel-occurrence-title"
            className="grid gap-3 rounded border border-slate-200 p-4"
            onSubmit={(event) => {
              event.preventDefault();
              const reason = String(
                new FormData(event.currentTarget).get("reason") ?? "",
              );
              void command(
                () =>
                  cancelClassOccurrence(
                    cancellingOccurrence.id,
                    { reason },
                    commandKey(
                      `occurrence:${cancellingOccurrence.id}:${reason}`,
                    ),
                  ),
                "Occurrence cancelled.",
              ).then((saved) => {
                if (saved) setCancellingOccurrence(null);
              });
            }}
            role="dialog"
          >
            <h3 className="font-semibold" id="cancel-occurrence-title">
              Cancel occurrence
            </h3>
            <ReasonField label="Occurrence cancellation reason" />
            <button className="button" disabled={pending} type="submit">
              Confirm occurrence cancellation
            </button>
          </form>
        ) : null}
      </section>

      <section aria-labelledby="class-bookings-title" className="grid gap-3">
        <h2 className="text-xl font-semibold" id="class-bookings-title">
          Class bookings
        </h2>
        <MemberSearch
          action="Search class booking members"
          label="Filter class bookings by member"
          onSelect={setBookingMember}
          selected={bookingMember}
        />
        {bookingMember ? (
          <button
            className="underline"
            onClick={() => setBookingMember(null)}
            type="button"
          >
            Clear member filter
          </button>
        ) : null}
        {bookings.pending ? <Loading label="Loading class bookings." /> : null}
        <Alert message={bookings.error} />
        {!bookings.pending && bookings.rows.length === 0 ? (
          <Empty label="No class bookings are in this range." />
        ) : (
          <ul className="grid gap-3">
            {bookings.rows.map((row) => (
              <li className="rounded border border-slate-200 p-3" key={row.id}>
                <p>
                  {row.starts_at}–{row.ends_at}{" "}
                  <StatusText status={row.status} />
                </p>
                <p className="muted">
                  Member {row.member_id} · occurrence {row.class_occurrence_id}{" "}
                  ·{" "}
                  {row.unmetered
                    ? `Unmetered · ${row.unmetered_reason}`
                    : `Package ${row.member_service_package_id ?? "missing"}`}
                </p>
                {row.status === "BOOKED" ? (
                  <div className="mt-2 flex flex-wrap gap-3">
                    {canAttend ? (
                      <>
                        <button
                          className="underline"
                          disabled={pending}
                          onClick={() =>
                            void command(
                              () =>
                                attendClassBooking(
                                  row.id,
                                  commandKey(`attend:${row.id}`),
                                ),
                              "Attendance recorded.",
                            )
                          }
                          type="button"
                        >
                          Mark attended
                        </button>
                        <button
                          className="underline"
                          disabled={pending}
                          onClick={() =>
                            void command(
                              () =>
                                noShowClassBooking(
                                  row.id,
                                  commandKey(`noshow:${row.id}`),
                                ),
                              "No-show recorded.",
                            )
                          }
                          type="button"
                        >
                          Mark no-show
                        </button>
                      </>
                    ) : null}
                    {canCancelBooking ? (
                      <button
                        className="underline"
                        disabled={pending}
                        onClick={() => setCancellingBooking(row)}
                        type="button"
                      >
                        Cancel booking
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        {canLateCancel ? (
          <p className="muted">
            A manager or owner with class management can cancel inside the
            cutoff. That late cancellation requires the reason you enter.
          </p>
        ) : null}
        {cancellingBooking && canCancelBooking ? (
          <form
            aria-labelledby="cancel-class-booking-title"
            className="grid gap-3 rounded border border-slate-200 p-4"
            onSubmit={(event) => {
              event.preventDefault();
              const reason = String(
                new FormData(event.currentTarget).get("reason") ?? "",
              );
              void command(
                () =>
                  cancelClassBooking(
                    cancellingBooking.id,
                    { reason },
                    commandKey(
                      `cancel-class:${cancellingBooking.id}:${reason}`,
                    ),
                  ),
                "Booking cancelled.",
              ).then((saved) => {
                if (saved) setCancellingBooking(null);
              });
            }}
            role="dialog"
          >
            <h3 className="font-semibold" id="cancel-class-booking-title">
              Cancel class booking
            </h3>
            <p className="muted">
              {canLateCancel
                ? "Enter a reason. A cancellation inside the cutoff is rejected without one."
                : "The reason is optional. Only the text entered here is sent."}
            </p>
            <ReasonField
              label="Class booking cancellation reason"
              required={canLateCancel}
            />
            <button className="button" disabled={pending} type="submit">
              Confirm class booking cancellation
            </button>
          </form>
        ) : null}
        <CursorControls
          bounded={bookings.bounded}
          hasMore={bookings.hasMore}
          index={bookings.index}
          onNext={() => void bookings.goNext()}
          onPrevious={() => void bookings.goPrevious()}
          pending={bookings.pending}
        />
        {canBook || canManage ? (
          <form
            aria-labelledby="book-class-title"
            className="grid gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              if (!member) {
                setError("Select an active member.");
                return;
              }
              const form = new FormData(event.currentTarget);
              const packageId = String(
                form.get("member_service_package_id") ?? "",
              );
              const body = {
                class_occurrence_id: String(
                  form.get("class_occurrence_id") ?? "",
                ),
                member_id: member.id,
                unmetered: canUnmetered && unmetered,
                unmetered_reason:
                  canUnmetered && unmetered
                    ? String(form.get("unmetered_reason") ?? "")
                    : "",
                ...(canUnmetered && unmetered
                  ? {}
                  : { member_service_package_id: packageId }),
              };
              void command(
                () =>
                  createClassBooking(body, commandKey(JSON.stringify(body))),
                "Class booked.",
              );
            }}
          >
            <h3 className="font-semibold" id="book-class-title">
              Book a class
            </h3>
            <Field label="Occurrence">
              <SelectInput name="class_occurrence_id" required>
                <option value="">Select an occurrence</option>
                {occurrences.rows
                  .filter((row) => row.status === "SCHEDULED")
                  .map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.local_date} · {row.starts_at}–{row.ends_at} ·{" "}
                      {row.location} · type {row.class_type_id} · capacity{" "}
                      {row.capacity}
                    </option>
                  ))}
              </SelectInput>
            </Field>
            <Alert message={packageNotice} />
            <MemberSearch
              disabled={pending}
              onSelect={(picked) => {
                setMember(picked);
                setPackageNotice("");
                void collectLookupPages((after) =>
                  listMemberPackages({
                    limit: "100",
                    member_id: picked.id,
                    package_type: "CLASS",
                    status: "ACTIVE",
                    ...(after ? { after } : {}),
                  }),
                )
                  .then((result) => {
                    setPackages(
                      result.error && !result.bounded ? [] : result.items,
                    );
                    setPackageNotice(result.error);
                  })
                  .catch((caught: unknown) => {
                    setPackages([]);
                    setError(
                      phase5Error(caught, "Packages could not be loaded."),
                    );
                  });
              }}
              selected={member}
            />
            {canUnmetered ? (
              <label>
                <input
                  checked={unmetered}
                  onChange={(event) => setUnmetered(event.target.checked)}
                  type="checkbox"
                />{" "}
                Unmetered manager booking
              </label>
            ) : null}
            {canUnmetered && unmetered ? (
              <ReasonField label="Unmetered reason" name="unmetered_reason" />
            ) : (
              <Field label="Class package">
                <SelectInput name="member_service_package_id" required>
                  <option value="">Select a package</option>
                  {packages.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.package_code} · {row.package_name} ·{" "}
                      {row.remaining_sessions} left · {row.status}
                    </option>
                  ))}
                </SelectInput>
              </Field>
            )}
            <button className="button" disabled={pending} type="submit">
              Save class booking
            </button>
          </form>
        ) : null}
      </section>
    </div>
  );
}
