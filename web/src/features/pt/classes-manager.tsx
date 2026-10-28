"use client";
import { useTx } from "@/i18n/use-tx";

import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
  type Ref,
} from "react";

import {
  Alert,
  CursorControls,
  Field,
  ReasonField,
  SelectInput,
  TextInput,
} from "@/features/finance/ui";
import { useCursorPages } from "@/features/finance/paging";
import { formatAppDate } from "@/lib/date";
import { compactIdentifier, IdentifierValue } from "@/lib/ui/identifier";
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
const panelClass =
  "scroll-mt-24 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm";

type ClassDialogMode =
  | { kind: "create-type" }
  | { kind: "edit-type"; row: ClassType }
  | { kind: "create-schedule" }
  | { kind: "edit-schedule"; row: ClassSchedule }
  | { kind: "cancel-occurrence"; row: ClassOccurrence }
  | { kind: "cancel-booking"; row: ClassBooking }
  | { kind: "book" };

type ScheduleBody = {
  class_type_id: string;
  trainer_staff_profile_id: string;
  weekdays: number[];
  local_start_time: string;
  local_end_time: string;
  effective_from: string;
  effective_until: string;
  location: string;
  location_is_exclusive: boolean;
  capacity: number;
};

function shiftDate(iso: string, days: number): string {
  const [year, month, day] = iso.split("-").map(Number);
  const date = new Date(Date.UTC(year ?? 0, (month ?? 1) - 1, day ?? 1));
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function inclusiveDays(from: string, to: string): number | null {
  const start = Date.parse(`${from}T00:00:00Z`);
  const end = Date.parse(`${to}T00:00:00Z`);
  if (Number.isNaN(start) || Number.isNaN(end)) return null;
  return Math.round((end - start) / 86_400_000) + 1;
}

function formatLocalDate(iso: string): string {
  return formatAppDate(iso);
}

function wallClock(value: string): string {
  const match = /(\d{2}):(\d{2})/.exec(value);
  if (!match?.[1] || !match[2]) return value;
  const date = new Date(
    Date.UTC(2026, 0, 1, Number(match[1]), Number(match[2])),
  );
  return new Intl.DateTimeFormat("en", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: "UTC",
  }).format(date);
}

function zonedClock(value: string, timeZone: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en", {
    hour: "numeric",
    minute: "2-digit",
    timeZone,
  }).format(date);
}

function inputClock(value: string): string {
  const matched = /T(\d{2}:\d{2})/.exec(value);
  if (matched?.[1]) return matched[1];
  return value.slice(0, 5);
}

function statusTone(status: string): string {
  switch (status) {
    case "ACTIVE":
    case "SCHEDULED":
    case "BOOKED":
      return "bg-sky-100 text-sky-800";
    case "ATTENDED":
      return "bg-emerald-100 text-emerald-800";
    case "NO_SHOW":
      return "bg-amber-100 text-amber-900";
    case "CANCELLED":
      return "bg-slate-200 text-slate-700";
    default:
      return "bg-slate-100 text-slate-700";
  }
}

function weekdaySummary(days: number[]): string {
  const labels = WEEKDAYS.filter((_, index) => days.includes(index));
  return labels.length > 0 ? labels.join(", ") : "No weekdays";
}

function scheduleBody(form: FormData): ScheduleBody {
  return {
    class_type_id: String(form.get("class_type_id") ?? ""),
    trainer_staff_profile_id: String(
      form.get("trainer_staff_profile_id") ?? "",
    ),
    weekdays: WEEKDAYS.map((_, index) => index).filter((index) =>
      form.get(`weekday_${index}`),
    ),
    local_start_time: String(form.get("local_start_time") ?? ""),
    local_end_time: String(form.get("local_end_time") ?? ""),
    effective_from: String(form.get("effective_from") ?? ""),
    effective_until: String(form.get("effective_until") ?? ""),
    location: String(form.get("location") ?? ""),
    location_is_exclusive: form.get("location_is_exclusive") === "on",
    capacity: Number(form.get("capacity")),
  };
}

function scheduleProblem(body: ScheduleBody): string {
  if (body.weekdays.length === 0) return "Choose at least one weekday.";
  if (body.local_end_time <= body.local_start_time) {
    return "End time must be after the start time.";
  }
  if (body.effective_until < body.effective_from) {
    return "The end date is before the start date.";
  }
  return "";
}

function dialogKeyOf(mode: ClassDialogMode | null): string {
  if (mode === null) return "";
  switch (mode.kind) {
    case "edit-type":
      return `edit-type:${mode.row.id}`;
    case "edit-schedule":
      return `edit-schedule:${mode.row.id}`;
    case "cancel-occurrence":
      return `cancel-occurrence:${mode.row.id}`;
    case "cancel-booking":
      return `cancel-booking:${mode.row.id}`;
    default:
      return mode.kind;
  }
}

function NoticeBanner({ message }: { message: string }) {
  const tx = useTx();
  return (
    <p
      aria-live="polite"
      className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-800"
      role="status"
    >
      {tx(message)}
    </p>
  );
}

function NamedId({ name, id }: { name: string | null; id: string }) {
  if (name) return name;
  return <IdentifierValue value={id} />;
}

function StatusPill({ status }: { status: string }) {
  const tx = useTx();
  return (
    <span
      className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-bold ${statusTone(status)}`}
    >
      {tx(status)}
    </span>
  );
}

function WeekdayPicker({ selected }: { selected: number[] | undefined }) {
  const tx = useTx();
  return (
    <fieldset className="min-w-0 border-0 p-0">
      <legend className="text-sm font-semibold text-slate-800">
        {tx("Weekdays")}
      </legend>
      <div className="mt-2 flex flex-wrap gap-2">
        {WEEKDAYS.map((label, index) => (
          <label
            className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-slate-50 px-3 py-1.5 text-sm font-semibold text-slate-700"
            key={label}
          >
            <input
              defaultChecked={selected?.includes(index)}
              name={`weekday_${index}`}
              type="checkbox"
            />
            {tx(label)}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function EmptyState({ title, body }: { title: string; body: string }) {
  const tx = useTx();
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-6 py-10 text-center">
      <p className="font-semibold text-slate-700">{tx(title)}</p>
      <p className="mt-1 text-sm leading-6 text-slate-500">{tx(body)}</p>
    </div>
  );
}

function LoadingRows({
  label,
  cards = false,
}: {
  label: string;
  cards?: boolean;
}) {
  const tx = useTx();
  return (
    <div className="grid gap-3" role="status">
      <p className="text-sm font-medium text-slate-600">{tx(label)}</p>
      <div
        className={
          cards ? "grid gap-3 sm:grid-cols-2 xl:grid-cols-3" : "grid gap-3"
        }
      >
        {[0, 1, 2].map((item) => (
          <div
            className="h-28 animate-pulse rounded-xl bg-slate-100"
            key={item}
          />
        ))}
      </div>
    </div>
  );
}

function LoadError({
  message,
  retry,
  onRetry,
}: {
  message: string;
  retry: string;
  onRetry: () => void;
}) {
  const tx = useTx();
  return (
    <div className="grid gap-3 rounded-xl border border-red-200 bg-red-50 p-4">
      <p className="text-sm font-medium text-red-800" role="alert">
        {tx(message)}
      </p>
      <button className="button w-fit" onClick={onRetry} type="button">
        {tx(retry)}
      </button>
    </div>
  );
}

function Paging({
  pending,
  index,
  hasMore,
  bounded,
  onPrevious,
  onNext,
}: {
  pending: boolean;
  index: number;
  hasMore: boolean;
  bounded: boolean;
  onPrevious: () => void;
  onNext: () => void;
}) {
  if (index === 0 && !hasMore && !bounded) return null;
  return (
    <div className="border-t border-slate-100 px-4 py-4 sm:px-5 [&_nav]:border-t-0 [&_nav]:pt-0">
      <CursorControls
        bounded={bounded}
        hasMore={hasMore}
        index={index}
        onNext={onNext}
        onPrevious={onPrevious}
        pending={pending}
      />
    </div>
  );
}

function viewTabClass(selected: boolean): string {
  const base = "min-h-10 rounded-full px-4 text-sm font-bold";
  return selected
    ? `${base} bg-slate-900 text-white`
    : `${base} border border-slate-300 bg-slate-100 text-slate-700 hover:bg-slate-200`;
}

function SectionIntro({
  title,
  titleId,
  description,
  action,
}: {
  title: string;
  titleId: string;
  description?: string;
  action?: ReactNode;
}) {
  const tx = useTx();
  return (
    <div className="flex flex-col gap-3 border-b border-slate-200 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
      <div className="min-w-0">
        <h2 className="text-lg font-bold text-slate-900" id={titleId}>
          {tx(title)}
        </h2>
        {description ? (
          <p className="mt-1 text-sm text-slate-500">{tx(description)}</p>
        ) : null}
      </div>
      {action}
    </div>
  );
}

function ClassDialog({
  title,
  titleId,
  description,
  descriptionId,
  dialogId,
  pending,
  error,
  submitLabel,
  submitClass,
  dialogRef,
  widthClass = "sm:max-w-2xl",
  onSubmit,
  onClose,
  children,
}: {
  title: string;
  titleId: string;
  description: string;
  descriptionId: string;
  dialogId: string;
  pending: boolean;
  error: string;
  submitLabel: string;
  submitClass: string;
  dialogRef: Ref<HTMLFormElement>;
  widthClass?: string;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onClose: () => void;
  children: ReactNode;
}) {
  const tx = useTx();
  return (
    <div
      className="fixed inset-0 z-50 grid overflow-hidden p-2 sm:place-items-center sm:p-6"
      onMouseDown={(event) => {
        if (event.target !== event.currentTarget) return;
        onClose();
      }}
      role="presentation"
    >
      <form
        aria-busy={pending}
        aria-describedby={descriptionId}
        aria-labelledby={titleId}
        aria-modal="true"
        className={`flex h-full max-h-full min-h-0 w-full flex-col overflow-y-auto overscroll-contain sm:h-auto sm:max-h-[min(90dvh,48rem)] ${widthClass}`}
        id={dialogId}
        onKeyDown={(event) => {
          if (event.key !== "Escape") return;
          event.stopPropagation();
          event.preventDefault();
          onClose();
        }}
        onSubmit={onSubmit}
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <div className="sticky top-0 z-10 flex items-start gap-3 border-b border-slate-100 bg-white px-5 py-4 sm:px-6">
          <div className="min-w-0 flex-1">
            <h2 className="text-xl font-bold text-slate-900" id={titleId}>
              {tx(title)}
            </h2>
            <p
              className="mt-1 text-sm leading-6 text-slate-500"
              id={descriptionId}
            >
              {tx(description)}
            </p>
          </div>
          <button
            className="button button-cancel button-sm"
            disabled={pending}
            onClick={onClose}
            type="button"
          >
            {" "}
            {tx("Close")}{" "}
          </button>
        </div>
        <fieldset
          className="m-0 grid w-full min-w-0 gap-5 border-0 px-5 py-4 sm:px-6"
          disabled={pending}
        >
          {children}
        </fieldset>
        <div className="sticky bottom-0 z-10 mt-auto border-t border-slate-100 bg-white px-5 py-4 sm:px-6">
          {error ? (
            <div className="mb-3">
              <Alert message={error} />
            </div>
          ) : null}
          <div className="flex flex-col sm:flex-row">
            <button
              className="button button-cancel w-full sm:w-auto"
              disabled={pending}
              onClick={onClose}
              type="button"
            >
              {" "}
              {tx("Cancel")}{" "}
            </button>
            <button
              className={`button w-full sm:w-auto ${submitClass}`}
              disabled={pending}
              type="submit"
            >
              {tx(submitLabel)}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}

function ScheduleFields({
  schedule,
  types,
  trainers,
  typesPending,
  trainersPending,
  typesError,
  trainersError,
  today,
}: {
  schedule?: ClassSchedule;
  types: ClassType[];
  trainers: TrainerIdentity[];
  typesPending: boolean;
  trainersPending: boolean;
  typesError: string;
  trainersError: string;
  today: string;
}) {
  const tx = useTx();
  return (
    <>
      <div className="grid gap-3">
        <div>
          <h3 className="text-sm font-bold text-slate-800">
            {" "}
            {tx("Class and trainer")}{" "}
          </h3>
          <p className="mt-1 text-xs leading-5 text-slate-500">
            {" "}
            {tx(
              "Choose an active class type and the trainer who leads it.",
            )}{" "}
          </p>
        </div>
        {typesPending || trainersPending ? (
          <p className="text-sm text-slate-500" role="status">
            {" "}
            {tx("Loading choices.")}{" "}
          </p>
        ) : null}
        <Alert message={typesError || trainersError} />
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Class type">
            <SelectInput
              defaultValue={schedule?.class_type_id}
              name="class_type_id"
              required
            >
              <option value="">{tx("Select a type")}</option>
              {schedule &&
              !types.some((row) => row.id === schedule.class_type_id) ? (
                <option value={schedule.class_type_id}>
                  {" "}
                  {tx("Current class type")}{" "}
                </option>
              ) : null}
              {types.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.code} · {row.name}
                </option>
              ))}
            </SelectInput>
          </Field>
          <Field label="Trainer">
            <SelectInput
              defaultValue={schedule?.trainer_staff_profile_id}
              name="trainer_staff_profile_id"
              required
            >
              <option value="">{tx("Select a trainer")}</option>
              {schedule &&
              !trainers.some(
                (row) => row.id === schedule.trainer_staff_profile_id,
              ) ? (
                <option value={schedule.trainer_staff_profile_id}>
                  {" "}
                  {tx("Current trainer")}{" "}
                </option>
              ) : null}
              {trainers.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.display_name} ({row.staff_code})
                </option>
              ))}
            </SelectInput>
          </Field>
        </div>
      </div>
      <div className="grid gap-3 border-t border-slate-100 pt-5">
        <div>
          <h3 className="text-sm font-bold text-slate-800">
            {tx("Weekly time")}
          </h3>
          <p className="mt-1 text-xs leading-5 text-slate-500">
            {" "}
            {tx(
              "Pick at least one weekday. The end time is later the same day.",
            )}{" "}
          </p>
        </div>
        <WeekdayPicker selected={schedule?.weekdays} />
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Start time">
            <TextInput
              defaultValue={
                schedule ? inputClock(schedule.local_start_time) : ""
              }
              name="local_start_time"
              required
              type="time"
            />
          </Field>
          <Field label="End time">
            <TextInput
              defaultValue={schedule ? inputClock(schedule.local_end_time) : ""}
              name="local_end_time"
              required
              type="time"
            />
          </Field>
        </div>
      </div>
      <div className="grid gap-3 border-t border-slate-100 pt-5">
        <div>
          <h3 className="text-sm font-bold text-slate-800">
            {tx("Dates and place")}
          </h3>
          <p className="mt-1 text-xs leading-5 text-slate-500">
            {" "}
            {tx("Occurrences are generated inside this gym-local range.")}{" "}
          </p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Effective from">
            <TextInput
              defaultValue={schedule?.effective_from ?? today}
              name="effective_from"
              required
              type="date"
            />
          </Field>
          <Field label="Effective until">
            <TextInput
              defaultValue={schedule?.effective_until}
              name="effective_until"
              required
              type="date"
            />
          </Field>
          <Field label="Location">
            <TextInput
              defaultValue={schedule?.location}
              name="location"
              required
            />
          </Field>
          <Field label="Capacity">
            <TextInput
              defaultValue={schedule?.capacity}
              min={1}
              name="capacity"
              required
              type="number"
            />
          </Field>
        </div>
        <label className="flex items-center gap-2 text-sm font-semibold text-slate-800">
          <input
            defaultChecked={schedule?.location_is_exclusive}
            name="location_is_exclusive"
            type="checkbox"
          />{" "}
          {tx("Exclusive location")}{" "}
        </label>
      </div>
    </>
  );
}

export function ClassesManager({
  canManage,
  canBook,
  canAttend,
  canUnmetered,
  canLateCancel,
  today,
  timeZone = "UTC",
}: {
  canManage: boolean;
  canBook: boolean;
  canAttend: boolean;
  canUnmetered: boolean;
  canLateCancel: boolean;
  today: string;
  timeZone?: string;
}) {
  const tx = useTx();
  const defaultTo = shiftDate(today, 6);
  const [dateFrom, setDateFrom] = useState(today);
  const [dateTo, setDateTo] = useState(defaultTo);
  const [draftFrom, setDraftFrom] = useState(today);
  const [draftTo, setDraftTo] = useState(defaultTo);
  const [rangeError, setRangeError] = useState("");
  const [bookingMember, setBookingMember] = useState<PickedMember | null>(null);
  const [member, setMember] = useState<PickedMember | null>(null);
  const [packages, setPackages] = useState<MemberServicePackage[]>([]);
  const [packagesPending, setPackagesPending] = useState(false);
  const [packageNotice, setPackageNotice] = useState("");
  const [unmetered, setUnmetered] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [dialogError, setDialogError] = useState("");
  const [pending, setPending] = useState(false);
  const [mode, setMode] = useState<ClassDialogMode | null>(null);
  const [view, setView] = useState<"week" | "schedules" | "types">("week");
  const key = useRef<{ fingerprint: string; value: string } | null>(null);
  const pendingRef = useRef(false);
  const packageToken = useRef(0);
  const dialogRef = useRef<HTMLFormElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);
  const dialogWasOpen = useRef(false);
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
  const dialogKey = dialogKeyOf(mode);
  const canCancelBooking = canBook || canManage;
  const canCreateBooking = canBook || canManage;
  const rangeChanged = dateFrom !== today || dateTo !== defaultTo;
  const bookingsFiltered = rangeChanged || bookingMember !== null;
  const scheduled = occurrences.rows.filter(
    (row) => row.status === "SCHEDULED",
  );

  useEffect(() => {
    if (dialogKey) {
      dialogWasOpen.current = true;
      dialogRef.current?.focus({ preventScroll: true });
      return;
    }
    if (!dialogWasOpen.current) return;
    dialogWasOpen.current = false;
    restoreRef.current?.focus();
  }, [dialogKey]);

  function commandKey(fingerprint: string): string {
    if (!key.current || key.current.fingerprint !== fingerprint) {
      key.current = { fingerprint, value: crypto.randomUUID() };
    }
    return key.current.value;
  }

  function typeName(id: string): string | null {
    const match =
      types.rows.find((row) => row.id === id) ??
      activeTypes.items.find((row) => row.id === id);
    return match ? `${match.code} · ${match.name}` : null;
  }

  function trainerName(id: string): string | null {
    const match = trainers.items.find((row) => row.id === id);
    return match ? `${match.display_name} (${match.staff_code})` : null;
  }

  function occurrenceClock(row: ClassOccurrence): string {
    const schedule = schedules.rows.find(
      (item) => item.id === row.class_schedule_id,
    );
    if (schedule) {
      return `${wallClock(schedule.local_start_time)} – ${wallClock(schedule.local_end_time)}`;
    }
    return `${zonedClock(row.starts_at, timeZone)} – ${zonedClock(row.ends_at, timeZone)}`;
  }

  function bookingClock(row: ClassBooking): string {
    const occurrence = occurrences.rows.find(
      (item) => item.id === row.class_occurrence_id,
    );
    if (occurrence) return occurrenceClock(occurrence);
    return `${zonedClock(row.starts_at, timeZone)} – ${zonedClock(row.ends_at, timeZone)}`;
  }

  function resetPackages() {
    packageToken.current += 1;
    setPackages([]);
    setPackagesPending(false);
    setPackageNotice("");
  }

  function closeDialog() {
    if (pendingRef.current) return;
    resetPackages();
    setDialogError("");
    setMode(null);
  }

  function openDialog(next: ClassDialogMode, opener: HTMLElement) {
    if (pendingRef.current) return;
    restoreRef.current = opener;
    setDialogError("");
    if (next.kind === "book") {
      resetPackages();
      setMember(null);
      setUnmetered(false);
    }
    setMode(next);
  }

  function chooseMember(picked: PickedMember) {
    const token = ++packageToken.current;
    setMember(picked);
    setPackages([]);
    setPackageNotice("");
    setDialogError("");
    setPackagesPending(true);
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
        if (packageToken.current !== token) return;
        setPackages(result.error && !result.bounded ? [] : result.items);
        setPackageNotice(result.error);
      })
      .catch((caught: unknown) => {
        if (packageToken.current !== token) return;
        setPackages([]);
        setDialogError(
          phase5Error(caught, tx("Packages could not be loaded.")),
        );
      })
      .finally(() => {
        if (packageToken.current === token) setPackagesPending(false);
      });
  }

  async function command(
    work: () => Promise<unknown>,
    success: string,
    scope: "page" | "dialog",
  ): Promise<boolean> {
    if (pendingRef.current) return false;
    pendingRef.current = true;
    setPending(true);
    setError("");
    setDialogError("");
    setNotice("");
    try {
      await work();
      key.current = null;
      setNotice(success);
      if (scope === "dialog") setMode(null);
      types.reload();
      schedules.reload();
      occurrences.reload();
      bookings.reload();
      activeTypes.reload();
      return true;
    } catch (caught) {
      const message = phase5Error(caught, tx("The class command failed."));
      if (scope === "dialog") setDialogError(message);
      else setError(message);
      return false;
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  }

  function applyRange(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draftFrom || !draftTo || draftTo < draftFrom) {
      setRangeError("The end date is before the start date.");
      return;
    }
    const days = inclusiveDays(draftFrom, draftTo);
    if (days === null || days > 42) {
      setRangeError("The calendar range is at most 42 days.");
      return;
    }
    setRangeError("");
    setDateFrom(draftFrom);
    setDateTo(draftTo);
  }

  function resetRange() {
    setDraftFrom(today);
    setDraftTo(defaultTo);
    setDateFrom(today);
    setDateTo(defaultTo);
    setRangeError("");
  }

  function submitCreateType(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    void command(
      () =>
        createClassType({
          name: String(form.get("name") ?? ""),
          description: String(form.get("description") ?? ""),
          default_duration_minutes: Number(
            form.get("default_duration_minutes"),
          ),
          default_capacity: Number(form.get("default_capacity")),
        }),
      "Class type created.",
      "dialog",
    );
  }

  function submitEditType(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (mode?.kind !== "edit-type") return;
    const editing = mode.row;
    const form = new FormData(event.currentTarget);
    void command(
      () =>
        updateClassType(editing.id, {
          name: String(form.get("name") ?? ""),
          description: String(form.get("description") ?? ""),
          default_duration_minutes: Number(
            form.get("default_duration_minutes"),
          ),
          default_capacity: Number(form.get("default_capacity")),
          is_active: form.get("is_active") === "true",
        }),
      "Class type updated.",
      "dialog",
    );
  }

  function submitSchedule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const editing = mode?.kind === "edit-schedule" ? mode.row : null;
    const body = scheduleBody(new FormData(event.currentTarget));
    const problem = scheduleProblem(body);
    if (problem) {
      setDialogError(problem);
      return;
    }
    if (editing) {
      void command(
        () =>
          updateClassSchedule(
            editing.id,
            body,
            commandKey(JSON.stringify({ id: editing.id, ...body })),
          ),
        "Schedule updated.",
        "dialog",
      );
      return;
    }
    void command(
      () => createClassSchedule(body, commandKey(JSON.stringify(body))),
      "Schedule created.",
      "dialog",
    );
  }

  function submitCancelOccurrence(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (mode?.kind !== "cancel-occurrence") return;
    const row = mode.row;
    const reason = String(
      new FormData(event.currentTarget).get("reason") ?? "",
    );
    void command(
      () =>
        cancelClassOccurrence(
          row.id,
          { reason },
          commandKey(`occurrence:${row.id}:${reason}`),
        ),
      "Occurrence cancelled.",
      "dialog",
    );
  }

  function submitCancelBooking(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (mode?.kind !== "cancel-booking") return;
    const row = mode.row;
    const reason = String(
      new FormData(event.currentTarget).get("reason") ?? "",
    );
    void command(
      () =>
        cancelClassBooking(
          row.id,
          { reason },
          commandKey(`cancel-class:${row.id}:${reason}`),
        ),
      "Booking cancelled.",
      "dialog",
    );
  }

  function submitBook(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!member) {
      setDialogError("Select an active member.");
      return;
    }
    const form = new FormData(event.currentTarget);
    const packageId = String(form.get("member_service_package_id") ?? "");
    const metered = !(canUnmetered && unmetered);
    if (metered && packagesPending) {
      setDialogError("Packages are still loading.");
      return;
    }
    const unmeteredReason =
      canUnmetered && unmetered
        ? String(form.get("unmetered_reason") ?? "")
        : "";
    const body = {
      class_occurrence_id: String(form.get("class_occurrence_id") ?? ""),
      member_id: member.id,
      unmetered: canUnmetered && unmetered,
      unmetered_reason: unmeteredReason,
      ...(metered ? { member_service_package_id: packageId } : {}),
    };
    void command(
      () => createClassBooking(body, commandKey(JSON.stringify(body))),
      "Class booked.",
      "dialog",
    );
  }

  function attend(row: ClassBooking) {
    if (pendingRef.current || !canAttend || row.status !== "BOOKED") return;
    void command(
      () => attendClassBooking(row.id, commandKey(`attend:${row.id}`)),
      "Attendance recorded.",
      "page",
    );
  }

  function markNoShow(row: ClassBooking) {
    if (pendingRef.current || !canAttend || row.status !== "BOOKED") return;
    void command(
      () => noShowClassBooking(row.id, commandKey(`noshow:${row.id}`)),
      "No-show recorded.",
      "page",
    );
  }

  return (
    <div className="grid gap-6">
      <div className="grid gap-6" inert={mode !== null}>
        {notice ? <NoticeBanner message={notice} /> : null}
        {error ? <Alert message={error} /> : null}
        <div
          aria-label={tx("Class sections")}
          className="flex flex-wrap gap-2"
          role="tablist"
        >
          {(
            [
              ["week", "This week"],
              ["schedules", "Schedules"],
              ["types", "Class types"],
            ] as const
          ).map(([id, label]) => (
            <button
              aria-controls={`${id}-panel`}
              aria-selected={view === id}
              className={viewTabClass(view === id)}
              id={`${id}-tab`}
              key={id}
              onClick={() => setView(id)}
              role="tab"
              type="button"
            >
              {tx(label)}
            </button>
          ))}
        </div>

        {view === "types" ? (
          <section
            aria-busy={types.pending}
            aria-labelledby="types-tab"
            className={panelClass}
            id="types-panel"
            role="tabpanel"
          >
            <SectionIntro
              action={
                canManage ? (
                  <button
                    aria-controls={
                      mode?.kind === "create-type"
                        ? "create-class-type-dialog"
                        : undefined
                    }
                    aria-expanded={mode?.kind === "create-type"}
                    className="button button-create w-full shrink-0 sm:w-auto"
                    disabled={pending}
                    onClick={(event) =>
                      openDialog({ kind: "create-type" }, event.currentTarget)
                    }
                    type="button"
                  >
                    <span aria-hidden="true">+</span>{" "}
                    {tx("Create class type")}{" "}
                  </button>
                ) : null
              }
              description="Duration and capacity for new schedules."
              title={tx("Class types")}
              titleId="class-types-title"
            />
            <div className="grid gap-4 p-4 sm:p-5">
              {types.pending && types.rows.length === 0 ? (
                <LoadingRows cards label="Loading class types." />
              ) : null}
              {types.pending && types.rows.length > 0 ? (
                <p className="sr-only" role="status">
                  {" "}
                  {tx("Loading class types.")}{" "}
                </p>
              ) : null}
              {!types.pending && types.error ? (
                <LoadError
                  message={types.error}
                  onRetry={() => types.reload()}
                  retry="Retry loading class types"
                />
              ) : null}
              {!types.pending && !types.error && types.rows.length === 0 ? (
                <EmptyState
                  body={
                    canManage
                      ? tx(
                          "Create a class type to set the duration and capacity.",
                        )
                      : tx("Class types will appear here when they are added.")
                  }
                  title={tx("No class types exist.")}
                />
              ) : null}
              {types.rows.length > 0 ? (
                <ul
                  aria-label={tx("Class types")}
                  className="divide-y divide-slate-100"
                >
                  {types.rows.map((row) => (
                    <li
                      className="flex flex-col gap-3 py-4 sm:flex-row sm:items-center sm:justify-between"
                      key={row.id}
                    >
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="text-base font-semibold break-words text-slate-900">
                            {row.name}
                          </h3>
                          <span
                            className={`rounded-full px-2.5 py-1 text-xs font-bold ${
                              row.is_active
                                ? "bg-emerald-100 text-emerald-700"
                                : "bg-slate-200 text-slate-600"
                            }`}
                          >
                            {row.is_active ? tx("Active") : tx("Archived")}
                          </span>
                        </div>
                        <p className="mt-1 text-sm break-words text-slate-500">
                          {row.code} · {row.default_duration_minutes}{" "}
                          {tx("min ·")} {row.default_capacity} {tx("spots")}
                          {row.description ? ` · ${row.description}` : ""}
                        </p>
                      </div>
                      {canManage ? (
                        <button
                          className="button button-edit button-sm w-full shrink-0 sm:w-auto"
                          disabled={pending}
                          onClick={(event) =>
                            openDialog(
                              { kind: "edit-type", row },
                              event.currentTarget,
                            )
                          }
                          type="button"
                        >
                          {" "}
                          {tx("Edit class type")}{" "}
                        </button>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
            <Paging
              bounded={types.bounded}
              hasMore={types.hasMore}
              index={types.index}
              onNext={() => void types.goNext()}
              onPrevious={() => void types.goPrevious()}
              pending={types.pending}
            />
          </section>
        ) : null}

        {view === "schedules" ? (
          <section
            aria-busy={schedules.pending}
            aria-labelledby="schedules-tab"
            className={panelClass}
            id="schedules-panel"
            role="tabpanel"
          >
            <SectionIntro
              action={
                canManage ? (
                  <button
                    aria-controls={
                      mode?.kind === "create-schedule"
                        ? "create-schedule-dialog"
                        : undefined
                    }
                    aria-expanded={mode?.kind === "create-schedule"}
                    className="button button-create w-full shrink-0 sm:w-auto"
                    disabled={pending}
                    onClick={(event) =>
                      openDialog(
                        { kind: "create-schedule" },
                        event.currentTarget,
                      )
                    }
                    type="button"
                  >
                    <span aria-hidden="true">+</span>{" "}
                    {tx("Create schedule")}{" "}
                  </button>
                ) : null
              }
              description="Saving regenerates future occurrences that nobody has booked."
              title={tx("Weekly schedules")}
              titleId="schedules-title"
            />
            <div className="grid gap-4 p-4 sm:p-5">
              {schedules.pending && schedules.rows.length === 0 ? (
                <LoadingRows label="Loading schedules." />
              ) : null}
              {schedules.pending && schedules.rows.length > 0 ? (
                <p className="sr-only" role="status">
                  {" "}
                  {tx("Loading schedules.")}{" "}
                </p>
              ) : null}
              {!schedules.pending && schedules.error ? (
                <LoadError
                  message={schedules.error}
                  onRetry={() => schedules.reload()}
                  retry="Retry loading schedules"
                />
              ) : null}
              {!schedules.pending &&
              !schedules.error &&
              schedules.rows.length === 0 ? (
                <EmptyState
                  body={
                    canManage
                      ? tx("Create a weekly schedule to generate occurrences.")
                      : tx("Schedules will appear here when they are added.")
                  }
                  title={tx("No schedules match.")}
                />
              ) : null}
              {schedules.rows.length > 0 ? (
                <ul
                  aria-label={tx("Weekly schedules")}
                  className="divide-y divide-slate-100"
                >
                  {schedules.rows.map((row) => (
                    <li
                      className="flex flex-col gap-3 py-4 sm:flex-row sm:items-center sm:justify-between"
                      key={row.id}
                    >
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="text-base font-semibold text-slate-900">
                            {weekdaySummary(row.weekdays)}
                            <span className="text-slate-400"> · </span>
                            <span className="tabular-nums">
                              {wallClock(row.local_start_time)} –{" "}
                              {wallClock(row.local_end_time)}
                            </span>
                          </h3>
                          <StatusPill status={row.status} />
                        </div>
                        <p className="mt-1 text-sm break-words text-slate-500">
                          {row.location}
                          {row.location_is_exclusive
                            ? tx("· Exclusive")
                            : ""} ·{" "}
                          <NamedId
                            id={row.class_type_id}
                            name={typeName(row.class_type_id)}
                          />
                          {" · "}
                          <NamedId
                            id={row.trainer_staff_profile_id}
                            name={trainerName(row.trainer_staff_profile_id)}
                          />
                          {" · "}
                          {row.capacity} {tx("spots ·")}{" "}
                          {formatLocalDate(row.effective_from)} –{" "}
                          {formatLocalDate(row.effective_until)}
                        </p>
                      </div>
                      {canManage && row.status === "ACTIVE" ? (
                        <button
                          className="button button-edit button-sm w-full shrink-0 sm:w-auto"
                          disabled={pending}
                          onClick={(event) =>
                            openDialog(
                              { kind: "edit-schedule", row },
                              event.currentTarget,
                            )
                          }
                          type="button"
                        >
                          {" "}
                          {tx("Update schedule")}{" "}
                        </button>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
            <Paging
              bounded={schedules.bounded}
              hasMore={schedules.hasMore}
              index={schedules.index}
              onNext={() => void schedules.goNext()}
              onPrevious={() => void schedules.goPrevious()}
              pending={schedules.pending}
            />
          </section>
        ) : null}

        {view === "week" ? (
          <section
            aria-labelledby="week-tab"
            className={panelClass}
            id="week-panel"
            role="tabpanel"
          >
            <div className="flex flex-col gap-4 border-b border-slate-200 px-4 py-4 sm:px-5">
              <form
                className="flex flex-col gap-3 lg:flex-row lg:items-end"
                onSubmit={applyRange}
              >
                <div className="sm:w-44">
                  <Field label="From">
                    <TextInput
                      name="date_from"
                      onChange={(event) => setDraftFrom(event.target.value)}
                      required
                      type="date"
                      value={draftFrom}
                    />
                  </Field>
                </div>
                <div className="sm:w-44">
                  <Field label="To">
                    <TextInput
                      name="date_to"
                      onChange={(event) => setDraftTo(event.target.value)}
                      required
                      type="date"
                      value={draftTo}
                    />
                  </Field>
                </div>
                <button className="button w-full sm:w-auto" type="submit">
                  {" "}
                  {tx("Show occurrences")}{" "}
                </button>
                {rangeChanged ? (
                  <button
                    className="text-sm font-semibold text-slate-600 underline-offset-4 hover:text-slate-900 hover:underline lg:mb-2"
                    onClick={resetRange}
                    type="button"
                  >
                    {" "}
                    {tx("Reset range")}{" "}
                  </button>
                ) : null}
              </form>
              {rangeError ? <Alert message={rangeError} /> : null}
              <p className="text-sm text-slate-500">
                {" "}
                {tx("Showing")} {formatLocalDate(dateFrom)} {tx("to")}{" "}
                {formatLocalDate(dateTo)}{" "}
                {tx(". The range can cover at most 42 days.")}{" "}
              </p>
            </div>
            <div>
              <section
                aria-busy={occurrences.pending}
                aria-labelledby="occurrences-title"
                className="border-b border-slate-200"
                id="occurrences"
              >
                <div className="px-4 pt-4 sm:px-5">
                  <h3
                    className="text-sm font-semibold text-slate-900"
                    id="occurrences-title"
                  >
                    {" "}
                    {tx("Occurrences")}{" "}
                  </h3>
                </div>
                <div className="grid gap-4 px-4 py-4 sm:px-5">
                  {occurrences.pending && occurrences.rows.length === 0 ? (
                    <LoadingRows label="Loading occurrences." />
                  ) : null}
                  {occurrences.pending && occurrences.rows.length > 0 ? (
                    <p className="sr-only" role="status">
                      {" "}
                      {tx("Loading occurrences.")}{" "}
                    </p>
                  ) : null}
                  {!occurrences.pending && occurrences.error ? (
                    <LoadError
                      message={occurrences.error}
                      onRetry={() => occurrences.reload()}
                      retry="Retry loading occurrences"
                    />
                  ) : null}
                  {!occurrences.pending &&
                  !occurrences.error &&
                  occurrences.rows.length === 0 ? (
                    <EmptyState
                      body="Try another gym-local range, up to 42 days."
                      title={tx("No occurrences are in this range.")}
                    />
                  ) : null}
                  {occurrences.rows.length > 0 ? (
                    <ul
                      aria-label={tx("Occurrences")}
                      className="divide-y divide-slate-100"
                    >
                      {occurrences.rows.map((row) => (
                        <li
                          className="flex flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between"
                          key={row.id}
                        >
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <h4 className="text-base font-semibold text-slate-900">
                                {formatLocalDate(row.local_date)}
                                <span className="text-slate-400"> · </span>
                                <span className="tabular-nums">
                                  {occurrenceClock(row)}
                                </span>
                              </h4>
                              <StatusPill status={row.status} />
                            </div>
                            <p className="mt-1 text-sm break-words text-slate-500">
                              {row.location}
                              {row.location_is_exclusive
                                ? tx("· Exclusive")
                                : ""}{" "}
                              ·{" "}
                              <NamedId
                                id={row.class_type_id}
                                name={typeName(row.class_type_id)}
                              />
                              {" · "}
                              <NamedId
                                id={row.trainer_staff_profile_id}
                                name={trainerName(row.trainer_staff_profile_id)}
                              />
                              {" · "}
                              {row.capacity} {tx("spots")}
                            </p>
                          </div>
                          {canManage && row.status === "SCHEDULED" ? (
                            <button
                              className="button button-cancel button-sm w-full shrink-0 sm:w-auto"
                              disabled={pending}
                              onClick={(event) =>
                                openDialog(
                                  { kind: "cancel-occurrence", row },
                                  event.currentTarget,
                                )
                              }
                              type="button"
                            >
                              {" "}
                              {tx("Cancel occurrence")}{" "}
                            </button>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
                <Paging
                  bounded={occurrences.bounded}
                  hasMore={occurrences.hasMore}
                  index={occurrences.index}
                  onNext={() => void occurrences.goNext()}
                  onPrevious={() => void occurrences.goPrevious()}
                  pending={occurrences.pending}
                />
              </section>

              <section
                aria-busy={bookings.pending || pending}
                aria-labelledby="class-bookings-title"
                id="class-bookings"
              >
                <div className="flex flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
                  <div className="min-w-0">
                    <h3
                      className="text-sm font-semibold text-slate-900"
                      id="class-bookings-title"
                    >
                      {" "}
                      {tx("Bookings")}{" "}
                    </h3>
                    {canLateCancel ? (
                      <p className="mt-1 text-sm text-slate-500">
                        {" "}
                        {tx(
                          "A manager can cancel inside the cutoff with a reason.",
                        )}{" "}
                      </p>
                    ) : null}
                  </div>
                  {canCreateBooking ? (
                    <button
                      aria-controls={
                        mode?.kind === "book" ? "book-class-dialog" : undefined
                      }
                      aria-expanded={mode?.kind === "book"}
                      className="button button-create w-full shrink-0 xl:w-auto"
                      disabled={pending}
                      onClick={(event) =>
                        openDialog({ kind: "book" }, event.currentTarget)
                      }
                      type="button"
                    >
                      <span aria-hidden="true">+</span>{" "}
                      {tx("Book a class")}{" "}
                    </button>
                  ) : null}
                </div>
                <div className="border-y border-slate-100 bg-slate-50/70 px-4 py-4 sm:px-5">
                  <MemberSearch
                    label="Filter class bookings by member"
                    onSelect={setBookingMember}
                    selected={bookingMember}
                  />
                  {bookingMember ? (
                    <button
                      className="mt-3 text-sm font-semibold text-slate-600 underline-offset-4 hover:text-slate-900 hover:underline"
                      onClick={() => setBookingMember(null)}
                      type="button"
                    >
                      {" "}
                      {tx("Clear member filter")}{" "}
                    </button>
                  ) : null}
                </div>
                <div className="grid gap-4 p-4 sm:p-5">
                  {bookings.pending && bookings.rows.length === 0 ? (
                    <LoadingRows label="Loading class bookings." />
                  ) : null}
                  {bookings.pending && bookings.rows.length > 0 ? (
                    <p className="sr-only" role="status">
                      {" "}
                      {tx("Loading class bookings.")}{" "}
                    </p>
                  ) : null}
                  {pending && mode === null ? (
                    <p
                      className="text-sm font-medium text-slate-600"
                      role="status"
                    >
                      {" "}
                      {tx("Updating the booking.")}{" "}
                    </p>
                  ) : null}
                  {!bookings.pending && bookings.error ? (
                    <LoadError
                      message={bookings.error}
                      onRetry={() => bookings.reload()}
                      retry="Retry loading class bookings"
                    />
                  ) : null}
                  {!bookings.pending &&
                  !bookings.error &&
                  bookings.rows.length === 0 ? (
                    <EmptyState
                      body={
                        bookingsFiltered
                          ? tx("Try another range or clear the member filter.")
                          : canCreateBooking
                            ? tx("Book a class to add it to this range.")
                            : tx("Bookings in this range will appear here.")
                      }
                      title={tx("No class bookings are in this range.")}
                    />
                  ) : null}
                  {bookings.rows.length > 0 ? (
                    <ul
                      aria-label={tx("Class bookings")}
                      className="divide-y divide-slate-100"
                    >
                      {bookings.rows.map((row) => (
                        <li
                          className="flex flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between"
                          key={row.id}
                        >
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <h4 className="text-base font-semibold text-slate-900 tabular-nums">
                                {bookingClock(row)}
                              </h4>
                              <StatusPill status={row.status} />
                            </div>
                            <p className="mt-1 text-sm break-words text-slate-500">
                              {" "}
                              {tx("Member")}{" "}
                              <IdentifierValue value={row.member_id} />
                              {" · "}
                              {row.unmetered ? (
                                `${tx("Unmetered")} · ${tx(row.unmetered_reason)}`
                              ) : (
                                <IdentifierValue
                                  value={
                                    row.member_service_package_id ?? "missing"
                                  }
                                />
                              )}
                            </p>
                          </div>
                          {row.status === "BOOKED" &&
                          (canAttend || canCancelBooking) ? (
                            <div
                              aria-label={tx("Booking actions")}
                              className="flex shrink-0 flex-col gap-2 sm:flex-row"
                              role="group"
                            >
                              {canAttend ? (
                                <>
                                  <button
                                    className="button button-success button-sm w-full sm:w-auto"
                                    disabled={pending}
                                    onClick={() => attend(row)}
                                    type="button"
                                  >
                                    {" "}
                                    {tx("Mark attended")}{" "}
                                  </button>
                                  <button
                                    className="button button-warn button-sm w-full sm:w-auto"
                                    disabled={pending}
                                    onClick={() => markNoShow(row)}
                                    type="button"
                                  >
                                    {" "}
                                    {tx("Mark no-show")}{" "}
                                  </button>
                                </>
                              ) : null}
                              {canCancelBooking ? (
                                <button
                                  className="button button-cancel button-sm w-full sm:w-auto"
                                  disabled={pending}
                                  onClick={(event) =>
                                    openDialog(
                                      { kind: "cancel-booking", row },
                                      event.currentTarget,
                                    )
                                  }
                                  type="button"
                                >
                                  {" "}
                                  {tx("Cancel booking")}{" "}
                                </button>
                              ) : null}
                            </div>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
                <Paging
                  bounded={bookings.bounded}
                  hasMore={bookings.hasMore}
                  index={bookings.index}
                  onNext={() => void bookings.goNext()}
                  onPrevious={() => void bookings.goPrevious()}
                  pending={bookings.pending}
                />
              </section>
            </div>
          </section>
        ) : null}
      </div>

      {mode?.kind === "create-type" && canManage ? (
        <ClassDialog
          description="A code is assigned from the name and stays fixed."
          descriptionId="create-class-type-description"
          dialogId="create-class-type-dialog"
          dialogRef={dialogRef}
          error={dialogError}
          onClose={closeDialog}
          onSubmit={submitCreateType}
          pending={pending}
          submitClass="button-create"
          submitLabel="Create class type"
          title={tx("Create class type")}
          titleId="create-class-type-title"
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <Field label="Name">
                <TextInput name="name" required />
              </Field>
            </div>
            <div className="sm:col-span-2">
              <Field label="Description">
                <TextInput name="description" />
              </Field>
            </div>
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
          </div>
        </ClassDialog>
      ) : null}

      {mode?.kind === "edit-type" && canManage ? (
        <ClassDialog
          description={tx("Code {code} stays unchanged.", {
            code: mode.row.code,
          })}
          descriptionId="edit-class-type-description"
          dialogId="edit-class-type-dialog"
          dialogRef={dialogRef}
          error={dialogError}
          onClose={closeDialog}
          onSubmit={submitEditType}
          pending={pending}
          submitClass="button-edit"
          submitLabel="Save class type"
          title={tx("Edit class type")}
          titleId="edit-class-type-title"
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Name">
              <TextInput defaultValue={mode.row.name} name="name" required />
            </Field>
            <Field label="Status">
              <SelectInput
                defaultValue={mode.row.is_active ? "true" : "false"}
                name="is_active"
              >
                <option value="true">{tx("Active")}</option>
                <option value="false">{tx("Archived")}</option>
              </SelectInput>
            </Field>
            <div className="sm:col-span-2">
              <Field label="Description">
                <TextInput
                  defaultValue={mode.row.description}
                  name="description"
                />
              </Field>
            </div>
            <Field label="Default duration minutes">
              <TextInput
                defaultValue={mode.row.default_duration_minutes}
                min={1}
                name="default_duration_minutes"
                required
                type="number"
              />
            </Field>
            <Field label="Default capacity">
              <TextInput
                defaultValue={mode.row.default_capacity}
                min={1}
                name="default_capacity"
                required
                type="number"
              />
            </Field>
          </div>
        </ClassDialog>
      ) : null}

      {mode?.kind === "create-schedule" && canManage ? (
        <ClassDialog
          description="Occurrences are generated from this weekly pattern."
          descriptionId="create-schedule-description"
          dialogId="create-schedule-dialog"
          dialogRef={dialogRef}
          error={dialogError}
          onClose={closeDialog}
          onSubmit={submitSchedule}
          pending={pending}
          submitClass="button-create"
          submitLabel="Create schedule"
          title={tx("Create schedule")}
          titleId="create-schedule-title"
        >
          <ScheduleFields
            today={today}
            trainers={trainers.items}
            trainersError={trainers.error}
            trainersPending={trainers.pending}
            types={activeTypes.items}
            typesError={activeTypes.error}
            typesPending={activeTypes.pending}
          />
        </ClassDialog>
      ) : null}

      {mode?.kind === "edit-schedule" && canManage ? (
        <ClassDialog
          description="Saving regenerates future occurrences that are still unbooked. Booked, attended, and no-show occurrences stay in place."
          descriptionId="edit-schedule-description"
          dialogId="edit-schedule-dialog"
          dialogRef={dialogRef}
          error={dialogError}
          key={mode.row.id}
          onClose={closeDialog}
          onSubmit={submitSchedule}
          pending={pending}
          submitClass="button-edit"
          submitLabel="Save schedule"
          title={tx("Update schedule")}
          titleId="edit-schedule-title"
        >
          <ScheduleFields
            schedule={mode.row}
            today={today}
            trainers={trainers.items}
            trainersError={trainers.error}
            trainersPending={trainers.pending}
            types={activeTypes.items}
            typesError={activeTypes.error}
            typesPending={activeTypes.pending}
          />
        </ClassDialog>
      ) : null}

      {mode?.kind === "cancel-occurrence" && canManage ? (
        <ClassDialog
          description="This occurrence will no longer accept bookings."
          descriptionId="cancel-occurrence-description"
          dialogId="cancel-occurrence-dialog"
          dialogRef={dialogRef}
          error={dialogError}
          onClose={closeDialog}
          onSubmit={submitCancelOccurrence}
          pending={pending}
          submitClass="button-cancel"
          submitLabel="Confirm occurrence cancellation"
          title={tx("Cancel occurrence")}
          titleId="cancel-occurrence-title"
          widthClass="sm:max-w-lg"
        >
          <p className="text-sm text-slate-700">
            {formatLocalDate(mode.row.local_date)} · {occurrenceClock(mode.row)}{" "}
            · {mode.row.location}
          </p>
          <ReasonField label="Occurrence cancellation reason" />
        </ClassDialog>
      ) : null}

      {mode?.kind === "cancel-booking" && canCancelBooking ? (
        <ClassDialog
          description={
            canLateCancel
              ? tx(
                  "Enter a reason. A cancellation inside the cutoff is rejected without one.",
                )
              : tx(
                  "The reason is optional. Only the text entered here is sent.",
                )
          }
          descriptionId="cancel-class-booking-description"
          dialogId="cancel-class-booking-dialog"
          dialogRef={dialogRef}
          error={dialogError}
          onClose={closeDialog}
          onSubmit={submitCancelBooking}
          pending={pending}
          submitClass="button-cancel"
          submitLabel="Confirm class booking cancellation"
          title={tx("Cancel class booking")}
          titleId="cancel-class-booking-title"
          widthClass="sm:max-w-lg"
        >
          <p className="text-sm text-slate-700">
            <time dateTime={mode.row.starts_at}>{bookingClock(mode.row)}</time>
          </p>
          <p className="text-sm text-slate-600">
            {" "}
            {tx("Member")} <IdentifierValue value={mode.row.member_id} />
          </p>
          <ReasonField
            label="Class booking cancellation reason"
            required={canLateCancel}
          />
        </ClassDialog>
      ) : null}

      {mode?.kind === "book" && canCreateBooking ? (
        <ClassDialog
          description="Choose a scheduled occurrence and an active class package."
          descriptionId="book-class-description"
          dialogId="book-class-dialog"
          dialogRef={dialogRef}
          error={dialogError}
          onClose={closeDialog}
          onSubmit={submitBook}
          pending={pending}
          submitClass="button-edit"
          submitLabel="Save class booking"
          title={tx("Book a class")}
          titleId="book-class-title"
        >
          <div className="grid gap-3">
            <div>
              <h3 className="text-sm font-bold text-slate-800">
                {tx("Occurrence")}
              </h3>
              <p className="mt-1 text-xs leading-5 text-slate-500">
                {" "}
                {tx(
                  "Only scheduled occurrences in the current range are listed.",
                )}{" "}
              </p>
            </div>
            {scheduled.length === 0 ? (
              <p className="text-sm text-slate-500">
                {" "}
                {tx("No scheduled occurrences are in this range.")}{" "}
              </p>
            ) : null}
            <Field label="Occurrence">
              <SelectInput name="class_occurrence_id" required>
                <option value="">{tx("Select an occurrence")}</option>
                {scheduled.map((row) => (
                  <option key={row.id} title={row.class_type_id} value={row.id}>
                    {formatAppDate(row.local_date)} · {occurrenceClock(row)} ·{" "}
                    {row.location} ·{" "}
                    {typeName(row.class_type_id) ??
                      compactIdentifier(row.class_type_id)}{" "}
                    {tx("· capacity")} {row.capacity}
                  </option>
                ))}
              </SelectInput>
            </Field>
          </div>
          <div className="grid gap-3 border-t border-slate-100 pt-5">
            <div>
              <h3 className="text-sm font-bold text-slate-800">
                {tx("Member")}
              </h3>
              <p className="mt-1 text-xs leading-5 text-slate-500">
                {" "}
                {tx("Search active members, then choose one.")}{" "}
              </p>
            </div>
            <MemberSearch
              disabled={pending}
              onSelect={chooseMember}
              selected={member}
            />
          </div>
          <div className="grid gap-3 border-t border-slate-100 pt-5">
            <div>
              <h3 className="text-sm font-bold text-slate-800">
                {tx("Package")}
              </h3>
              <p className="mt-1 text-xs leading-5 text-slate-500">
                {canUnmetered
                  ? tx(
                      "Use an active class package, or record an unmetered manager reason.",
                    )
                  : tx("The booking uses one session from this class package.")}
              </p>
            </div>
            {canUnmetered ? (
              <label className="flex items-center gap-2 text-sm font-semibold text-slate-800">
                <input
                  checked={unmetered}
                  onChange={(event) => {
                    setUnmetered(event.target.checked);
                    setDialogError("");
                  }}
                  type="checkbox"
                />{" "}
                {tx("Unmetered manager booking")}{" "}
              </label>
            ) : null}
            {canUnmetered && unmetered ? (
              <ReasonField label="Unmetered reason" name="unmetered_reason" />
            ) : (
              <>
                {packagesPending ? (
                  <p className="text-sm text-slate-500" role="status">
                    {" "}
                    {tx("Loading packages.")}{" "}
                  </p>
                ) : null}
                <Alert message={packageNotice} />
                {member &&
                !packagesPending &&
                !packageNotice &&
                packages.length === 0 ? (
                  <p className="text-sm text-slate-500">
                    {" "}
                    {tx(
                      "No active class packages are available for this member.",
                    )}{" "}
                  </p>
                ) : null}
                <Field label="Class package">
                  <SelectInput
                    key={member?.id ?? "none"}
                    name="member_service_package_id"
                    required
                  >
                    <option value="">{tx("Select a package")}</option>
                    {packages.map((row) => (
                      <option key={row.id} value={row.id}>
                        {row.package_code} · {row.package_name} ·{" "}
                        {row.remaining_sessions} {tx("left ·")} {tx(row.status)}
                      </option>
                    ))}
                  </SelectInput>
                </Field>
              </>
            )}
          </div>
        </ClassDialog>
      ) : null}
    </div>
  );
}
