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
import { formatInstant, zonedLocalToIso } from "@/lib/date";
import { IdentifierValue } from "@/lib/ui/identifier";
import {
  attendPtBooking,
  cancelPtBooking,
  createPtBooking,
  listMemberPackages,
  listPtBookings,
  listTrainers,
  noShowPtBooking,
} from "@/features/pt/client";
import { phase5Error } from "@/features/pt/errors";
import { collectLookupPages, useLookupOptions } from "@/features/pt/lookup";
import { MemberSearch, type PickedMember } from "@/features/pt/member-search";
import type {
  MemberServicePackage,
  PTBooking,
  TrainerIdentity,
} from "@/types/api";

const PAGE = "25";
const cardClass =
  "overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm";

function useMinWidth(px: number): boolean {
  const [matches, setMatches] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(`(min-width: ${px}px)`);
    const sync = () => setMatches(media.matches);
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, [px]);
  return matches;
}

function shiftDate(iso: string, days: number): string {
  const [year, month, day] = iso.split("-").map(Number);
  const date = new Date(Date.UTC(year ?? 0, (month ?? 1) - 1, day ?? 1));
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function bookingStatusClass(status: string): string {
  switch (status) {
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

function bookingBorderClass(status: string): string {
  switch (status) {
    case "BOOKED":
      return "border-l-sky-500";
    case "ATTENDED":
      return "border-l-emerald-500";
    case "NO_SHOW":
      return "border-l-amber-400";
    case "CANCELLED":
      return "border-l-slate-300";
    default:
      return "border-l-slate-300";
  }
}

function BookingStatusBadge({ status }: { status: string }) {
  const tx = useTx();
  return (
    <span
      className={`rounded-full px-2.5 py-1 text-xs font-bold ${bookingStatusClass(status)}`}
    >
      {tx(status)}
    </span>
  );
}

function packageCell(row: PTBooking, tx: (text: string) => string) {
  if (row.unmetered) {
    return `${tx("Unmetered")} · ${tx(row.unmetered_reason)}`;
  }
  return <IdentifierValue value={row.member_service_package_id ?? "missing"} />;
}

function BookingActions({
  row,
  selected,
  pending,
  onAttend,
  onNoShow,
  onCancel,
}: {
  row: PTBooking;
  selected: boolean;
  pending: boolean;
  onAttend: (row: PTBooking) => void;
  onNoShow: (row: PTBooking) => void;
  onCancel: (row: PTBooking, opener: HTMLElement) => void;
}) {
  const tx = useTx();
  return (
    <div
      aria-label={tx("Booking actions")}
      className="flex flex-col gap-2 sm:flex-row sm:flex-wrap"
      role="group"
    >
      <button
        className="button button-success button-sm w-full sm:w-auto"
        disabled={pending}
        onClick={() => onAttend(row)}
        type="button"
      >
        {" "}
        {tx("Mark attended")}{" "}
      </button>
      <button
        className="button button-warn button-sm w-full sm:w-auto"
        disabled={pending}
        onClick={() => onNoShow(row)}
        type="button"
      >
        {" "}
        {tx("Mark no-show")}{" "}
      </button>
      <button
        aria-controls={selected ? "cancel-pt-booking-dialog" : undefined}
        aria-expanded={selected}
        className="button button-cancel button-sm w-full sm:w-auto"
        disabled={pending}
        onClick={(event) => onCancel(row, event.currentTarget)}
        type="button"
      >
        {" "}
        {tx("Cancel booking")}{" "}
      </button>
    </div>
  );
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

function BookingDialog({
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
        className="flex h-full max-h-full min-h-0 w-full flex-col overflow-y-auto overscroll-contain sm:h-auto sm:max-h-[min(90dvh,48rem)] sm:max-w-2xl"
        id={dialogId}
        noValidate
        onKeyDown={(event) => {
          if (event.key !== "Escape") return;
          event.stopPropagation();
          event.preventDefault();
          onClose();
        }}
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit(event);
        }}
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <div className="sticky top-0 z-10 flex items-start gap-3 border-b border-slate-100 bg-white px-5 py-4 sm:px-6">
          <div className="min-w-0 flex-1">
            <h2 className="text-xl font-bold text-slate-900" id={titleId}>
              {tx(title)}
            </h2>
            <p className="mt-1 text-sm text-slate-500" id={descriptionId}>
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

export function BookingsManager({
  canManage,
  canUnmetered,
  today,
  timeZone,
}: {
  canManage: boolean;
  canUnmetered: boolean;
  today: string;
  timeZone: string;
}) {
  const tx = useTx();
  const defaultTo = shiftDate(today, 6);
  const [dateFrom, setDateFrom] = useState(today);
  const [dateTo, setDateTo] = useState(defaultTo);
  const [draftFrom, setDraftFrom] = useState(today);
  const [draftTo, setDraftTo] = useState(defaultTo);
  const [memberFilter, setMemberFilter] = useState<PickedMember | null>(null);
  const [member, setMember] = useState<PickedMember | null>(null);
  const [packages, setPackages] = useState<MemberServicePackage[]>([]);
  const [packagesPending, setPackagesPending] = useState(false);
  const [packageNotice, setPackageNotice] = useState("");
  const [cancelling, setCancelling] = useState<PTBooking | null>(null);
  const [notice, setNotice] = useState("");
  const [commandError, setCommandError] = useState("");
  const [dialogError, setDialogError] = useState("");
  const [pending, setPending] = useState(false);
  const [creating, setCreating] = useState(false);
  const [unmetered, setUnmetered] = useState(false);
  const key = useRef<{ fingerprint: string; value: string } | null>(null);
  const pendingRef = useRef(false);
  const packageToken = useRef(0);
  const dialogRef = useRef<HTMLFormElement>(null);
  const createButtonRef = useRef<HTMLButtonElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const dialogWasOpen = useRef(false);
  const bookings = useCursorPages<PTBooking>(
    `pt-bookings:${dateFrom}:${dateTo}:${memberFilter?.id ?? ""}`,
    (after) =>
      listPtBookings({
        limit: PAGE,
        view: "calendar",
        date_from: dateFrom,
        date_to: dateTo,
        ...(after ? { after } : {}),
        ...(memberFilter ? { member_id: memberFilter.id } : {}),
      }),
  );
  const trainers = useLookupOptions<TrainerIdentity>(
    "booking-trainers",
    (after) => listTrainers({ limit: "100", ...(after ? { after } : {}) }),
    creating,
  );
  const dialogKey = creating
    ? "create"
    : cancelling
      ? `cancel:${cancelling.id}`
      : "";
  const rangeChanged = dateFrom !== today || dateTo !== defaultTo;
  const filtered = rangeChanged || memberFilter !== null;
  const showPaging = bookings.index > 0 || bookings.hasMore || bookings.bounded;
  const wide = useMinWidth(768);

  useEffect(() => {
    if (dialogKey) {
      dialogWasOpen.current = true;
      dialogRef.current?.focus({ preventScroll: true });
      return;
    }
    if (!dialogWasOpen.current) return;
    dialogWasOpen.current = false;
    restoreFocusRef.current?.focus();
  }, [dialogKey]);

  function commandKey(fingerprint: string): string {
    if (!key.current || key.current.fingerprint !== fingerprint) {
      key.current = { fingerprint, value: crypto.randomUUID() };
    }
    return key.current.value;
  }

  function invalidatePackages() {
    packageToken.current += 1;
    setPackages([]);
    setPackagesPending(false);
    setPackageNotice("");
  }

  function closeDialog() {
    if (pendingRef.current) return;
    invalidatePackages();
    setDialogError("");
    setCreating(false);
    setCancelling(null);
  }

  function openCreate() {
    if (creating) return;
    restoreFocusRef.current = createButtonRef.current;
    invalidatePackages();
    setMember(null);
    setUnmetered(false);
    setDialogError("");
    setCancelling(null);
    setCreating(true);
  }

  function openCancel(row: PTBooking, opener: HTMLElement) {
    if (!canManage || row.status !== "BOOKED") return;
    if (cancelling?.id === row.id) return;
    restoreFocusRef.current = opener;
    if (creating) invalidatePackages();
    setDialogError("");
    setCreating(false);
    setCancelling(row);
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
        package_type: "PT",
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

  async function runCommand(
    work: () => Promise<unknown>,
    success: string,
    scope: "page" | "dialog",
  ): Promise<boolean> {
    if (pendingRef.current) return false;
    pendingRef.current = true;
    setPending(true);
    setCommandError("");
    setDialogError("");
    setNotice("");
    try {
      await work();
      key.current = null;
      setNotice(success);
      bookings.reload();
      return true;
    } catch (caught) {
      const message = phase5Error(caught, tx("The booking command failed."));
      if (scope === "dialog") setDialogError(message);
      else setCommandError(message);
      return false;
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  }

  function applyRange(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draftFrom || !draftTo) return;
    setDateFrom(draftFrom);
    setDateTo(draftTo);
  }

  function resetRange() {
    setDraftFrom(today);
    setDraftTo(defaultTo);
    setDateFrom(today);
    setDateTo(defaultTo);
  }

  function attend(row: PTBooking) {
    if (pendingRef.current || !canManage || row.status !== "BOOKED") return;
    void runCommand(
      () => attendPtBooking(row.id, commandKey(`attend:${row.id}`)),
      "Attendance recorded.",
      "page",
    );
  }

  function markNoShow(row: PTBooking) {
    if (pendingRef.current || !canManage || row.status !== "BOOKED") return;
    void runCommand(
      () => noShowPtBooking(row.id, commandKey(`noshow:${row.id}`)),
      "No-show recorded.",
      "page",
    );
  }

  function submitCancel(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!cancelling || pendingRef.current) return;
    const reason = String(
      new FormData(event.currentTarget).get("reason") ?? "",
    );
    const bookingId = cancelling.id;
    void runCommand(
      () =>
        cancelPtBooking(
          bookingId,
          { reason },
          commandKey(`cancel:${bookingId}:${reason}`),
        ),
      "Booking cancelled.",
      "dialog",
    ).then((saved) => {
      if (saved) setCancelling(null);
    });
  }

  function submitCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pendingRef.current) return;
    if (!member) {
      setDialogError("Select an active member.");
      return;
    }
    const form = new FormData(event.currentTarget);
    const trainerId = String(form.get("trainer_staff_profile_id") ?? "");
    if (!trainerId) {
      setDialogError("Select a trainer.");
      return;
    }
    const packageId = String(form.get("member_service_package_id") ?? "");
    const metered = !(canUnmetered && unmetered);
    if (metered && packagesPending) {
      setDialogError("Packages are still loading.");
      return;
    }
    if (metered && !packageId) {
      setDialogError("Select a package.");
      return;
    }
    const unmeteredReason =
      canUnmetered && unmetered
        ? String(form.get("unmetered_reason") ?? "")
        : "";
    if (canUnmetered && unmetered && unmeteredReason === "") {
      setDialogError("Enter an unmetered reason.");
      return;
    }
    let startsAt = "";
    let endsAt = "";
    try {
      startsAt = zonedLocalToIso(String(form.get("starts_at") ?? ""), timeZone);
      endsAt = zonedLocalToIso(String(form.get("ends_at") ?? ""), timeZone);
    } catch (caught) {
      setDialogError(
        caught instanceof Error ? caught.message : "Enter a valid time.",
      );
      return;
    }
    const body = {
      trainer_staff_profile_id: trainerId,
      member_id: member.id,
      starts_at: startsAt,
      ends_at: endsAt,
      unmetered: canUnmetered && unmetered,
      unmetered_reason: unmeteredReason,
      ...(canUnmetered && unmetered
        ? {}
        : { member_service_package_id: packageId }),
    };
    void runCommand(
      () => createPtBooking(body, commandKey(JSON.stringify(body))),
      "Session booked.",
      "dialog",
    ).then((saved) => {
      if (saved) setCreating(false);
    });
  }

  return (
    <div className="grid gap-6">
      <div className="grid gap-6" inert={dialogKey !== ""}>
        {notice ? <NoticeBanner message={notice} /> : null}
        {commandError ? <Alert message={commandError} /> : null}
        <section
          aria-busy={bookings.pending || pending}
          aria-labelledby="pt-bookings-list-title"
          className={cardClass}
        >
          <div className="flex flex-col gap-3 border-b border-slate-200 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
            <div>
              <h2
                className="text-sm font-bold tracking-wide text-slate-700 uppercase"
                id="pt-bookings-list-title"
              >
                {" "}
                {tx("Session calendar")}{" "}
              </h2>
              <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-500">
                {" "}
                {tx(
                  "Gym-local personal-training sessions. Attendance and no-show consume a package.",
                )}{" "}
              </p>
            </div>
            {canManage ? (
              <button
                aria-controls={creating ? "book-pt-dialog" : undefined}
                aria-expanded={creating}
                className="button button-create w-full sm:w-auto"
                disabled={pending}
                onClick={openCreate}
                ref={createButtonRef}
                type="button"
              >
                <span aria-hidden="true">+</span> {tx("Book session")}{" "}
              </button>
            ) : null}
          </div>

          <div className="grid gap-4 border-b border-slate-200 bg-slate-50/70 px-4 py-4 sm:px-5">
            <form
              className="flex flex-col gap-3 sm:flex-row sm:items-end"
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
                {tx("Show calendar")}{" "}
              </button>
              {rangeChanged ? (
                <button
                  className="text-sm font-semibold text-slate-600 underline-offset-4 hover:text-slate-900 hover:underline sm:mb-2"
                  onClick={resetRange}
                  type="button"
                >
                  {" "}
                  {tx("Reset range")}{" "}
                </button>
              ) : null}
            </form>
            <p className="text-sm text-slate-500">
              {" "}
              {tx("Showing")} {dateFrom} {tx("to")} {dateTo}
              {tx(". Calendar range is at most 42 gym-local days.")}{" "}
            </p>
            <MemberSearch
              label="Filter bookings by member"
              onSelect={setMemberFilter}
              selected={memberFilter}
            />
            {memberFilter ? (
              <button
                className="w-fit text-sm font-semibold text-slate-600 underline-offset-4 hover:text-slate-900 hover:underline"
                onClick={() => setMemberFilter(null)}
                type="button"
              >
                {" "}
                {tx("Clear member filter")}{" "}
              </button>
            ) : null}
          </div>

          <div className="grid gap-4 p-4 sm:p-5">
            {bookings.pending && bookings.rows.length === 0 ? (
              <div className="grid gap-3" role="status">
                <p className="text-sm font-medium text-slate-600">
                  {" "}
                  {tx("Loading bookings.")}{" "}
                </p>
                <div className="grid gap-3">
                  {[0, 1, 2].map((item) => (
                    <div
                      className="h-24 animate-pulse rounded-xl bg-slate-100"
                      key={item}
                    />
                  ))}
                </div>
              </div>
            ) : null}
            {bookings.pending && bookings.rows.length > 0 ? (
              <p className="text-sm font-medium text-slate-600" role="status">
                {" "}
                {tx("Loading bookings.")}{" "}
              </p>
            ) : null}
            {pending && !dialogKey ? (
              <p className="text-sm font-medium text-slate-600" role="status">
                {" "}
                {tx("Updating the booking.")}{" "}
              </p>
            ) : null}
            {!bookings.pending && bookings.error ? (
              <div className="grid gap-3 rounded-xl border border-red-200 bg-red-50 p-4">
                <p className="text-sm font-medium text-red-800" role="alert">
                  {bookings.error}
                </p>
                <button
                  className="button w-fit"
                  onClick={() => bookings.reload()}
                  type="button"
                >
                  {" "}
                  {tx("Retry loading bookings")}{" "}
                </button>
              </div>
            ) : null}
            {!bookings.pending &&
            !bookings.error &&
            bookings.rows.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-6 py-12 text-center">
                <p className="font-semibold text-slate-700">
                  {" "}
                  {tx("No PT bookings are in this range.")}{" "}
                </p>
                <p className="mt-1 text-sm text-slate-500">
                  {filtered
                    ? tx(
                        "Try another gym-local range or clear the member filter.",
                      )
                    : canManage
                      ? tx("Book a session to add it to this calendar.")
                      : tx("Sessions in this range will appear here.")}
                </p>
              </div>
            ) : null}
            {bookings.rows.length > 0 ? (
              <div className={bookings.pending ? "opacity-60" : undefined}>
                {!wide ? (
                  <ul aria-label={tx("PT bookings")} className="grid gap-3">
                    {bookings.rows.map((row) => {
                      const selected = cancelling?.id === row.id;
                      return (
                        <li
                          className={`rounded-xl border border-l-4 border-slate-200 p-4 ${bookingBorderClass(row.status)} ${
                            selected
                              ? "bg-teal-50 ring-2 ring-teal-600"
                              : row.status === "BOOKED"
                                ? "bg-white"
                                : "bg-slate-50"
                          }`}
                          key={row.id}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <p className="text-xs font-semibold tracking-wide text-slate-500">
                                {" "}
                                {tx("Session")}{" "}
                              </p>
                              <h3 className="text-base font-bold break-words text-slate-900">
                                <time dateTime={row.starts_at}>
                                  {formatInstant(row.starts_at, timeZone)}
                                </time>
                                <span aria-hidden="true"> – </span>
                                <time dateTime={row.ends_at}>
                                  {formatInstant(row.ends_at, timeZone)}
                                </time>
                              </h3>
                            </div>
                            <BookingStatusBadge status={row.status} />
                          </div>
                          <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-slate-100 pt-3">
                            <div className="min-w-0">
                              <dt className="text-xs text-slate-500">
                                {tx("Member")}
                              </dt>
                              <dd className="mt-0.5 font-semibold break-words text-slate-900">
                                {row.member_name || (
                                  <IdentifierValue value={row.member_id} />
                                )}
                              </dd>
                            </div>
                            <div className="min-w-0">
                              <dt className="text-xs text-slate-500">
                                {tx("Trainer")}
                              </dt>
                              <dd className="mt-0.5 font-semibold break-words text-slate-900">
                                {row.trainer_display_name || (
                                  <IdentifierValue
                                    value={row.trainer_staff_profile_id}
                                  />
                                )}
                              </dd>
                            </div>
                            <div className="col-span-2 min-w-0">
                              <dt className="text-xs text-slate-500">
                                {row.unmetered ? tx("Metering") : tx("Package")}
                              </dt>
                              <dd className="mt-0.5 font-semibold break-words text-slate-900">
                                {packageCell(row, tx)}
                              </dd>
                            </div>
                          </dl>
                          {row.reason ? (
                            <p className="mt-3 text-sm break-words text-slate-600">
                              {tx("Reason:")} {row.reason}
                            </p>
                          ) : null}
                          {canManage && row.status === "BOOKED" ? (
                            <div className="mt-4 border-t border-slate-100 pt-4">
                              <BookingActions
                                onAttend={attend}
                                onCancel={openCancel}
                                onNoShow={markNoShow}
                                pending={pending}
                                row={row}
                                selected={selected}
                              />
                            </div>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                ) : (
                  <div className="w-full overflow-x-auto">
                    <table className="w-full min-w-[48rem] text-left text-sm">
                      <caption className="sr-only">{tx("PT bookings")}</caption>
                      <thead className="border-b border-slate-200 text-slate-500">
                        <tr>
                          <th
                            className="px-4 py-2.5 text-xs font-semibold sm:px-5"
                            scope="col"
                          >
                            {tx("Session")}
                          </th>
                          <th
                            className="px-4 py-2.5 text-xs font-semibold sm:px-5"
                            scope="col"
                          >
                            {tx("Member")}
                          </th>
                          <th
                            className="px-4 py-2.5 text-xs font-semibold sm:px-5"
                            scope="col"
                          >
                            {tx("Trainer")}
                          </th>
                          <th
                            className="px-4 py-2.5 text-xs font-semibold sm:px-5"
                            scope="col"
                          >
                            {tx("Package")}
                          </th>
                          <th
                            className="px-4 py-2.5 text-xs font-semibold sm:px-5"
                            scope="col"
                          >
                            {tx("Status")}
                          </th>
                          <th
                            className="px-4 py-2.5 text-xs font-semibold sm:px-5"
                            scope="col"
                          >
                            <span className="sr-only">{tx("Actions")}</span>
                          </th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {bookings.rows.map((row) => {
                          const selected = cancelling?.id === row.id;
                          return (
                            <tr
                              className={`align-top ${
                                selected
                                  ? "bg-teal-50 shadow-[inset_3px_0_0_#0f766e]"
                                  : "hover:bg-slate-50"
                              }`}
                              key={row.id}
                            >
                              <td className="px-4 py-3 whitespace-nowrap text-slate-900 sm:px-5">
                                <p className="font-semibold">
                                  <time dateTime={row.starts_at}>
                                    {formatInstant(row.starts_at, timeZone)}
                                  </time>
                                </p>
                                <p className="mt-0.5 text-slate-600">
                                  <time dateTime={row.ends_at}>
                                    {formatInstant(row.ends_at, timeZone)}
                                  </time>
                                </p>
                                {row.reason ? (
                                  <p className="mt-1 max-w-[16rem] text-xs break-words text-slate-500">
                                    {tx("Reason:")} {row.reason}
                                  </p>
                                ) : null}
                              </td>
                              <td className="max-w-[10rem] px-4 py-3 break-words text-slate-800 sm:px-5">
                                {row.member_name || (
                                  <IdentifierValue value={row.member_id} />
                                )}
                              </td>
                              <td className="max-w-[10rem] px-4 py-3 break-words text-slate-800 sm:px-5">
                                {row.trainer_display_name || (
                                  <IdentifierValue
                                    value={row.trainer_staff_profile_id}
                                  />
                                )}
                              </td>
                              <td className="max-w-[12rem] px-4 py-3 break-words text-slate-800 sm:px-5">
                                {packageCell(row, tx)}
                              </td>
                              <td className="px-4 py-3 sm:px-5">
                                <BookingStatusBadge status={row.status} />
                              </td>
                              <td className="px-4 py-3 sm:px-5">
                                {canManage && row.status === "BOOKED" ? (
                                  <BookingActions
                                    onAttend={attend}
                                    onCancel={openCancel}
                                    onNoShow={markNoShow}
                                    pending={pending}
                                    row={row}
                                    selected={selected}
                                  />
                                ) : null}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            ) : null}
          </div>
          {showPaging ? (
            <div className="border-t border-slate-100 px-4 py-4 sm:px-5 [&_nav]:border-t-0 [&_nav]:pt-0">
              <CursorControls
                bounded={bookings.bounded}
                hasMore={bookings.hasMore}
                index={bookings.index}
                onNext={() => void bookings.goNext()}
                onPrevious={() => void bookings.goPrevious()}
                pending={bookings.pending}
              />
            </div>
          ) : null}
        </section>
      </div>

      {creating && canManage ? (
        <BookingDialog
          description="Choose the trainer, member, and gym-local time. A metered booking uses one active PT package."
          descriptionId="book-pt-description"
          dialogId="book-pt-dialog"
          dialogRef={dialogRef}
          error={dialogError}
          onClose={closeDialog}
          onSubmit={submitCreate}
          pending={pending}
          submitClass="button-edit"
          submitLabel="Save booking"
          title={tx("Book session")}
          titleId="book-pt-title"
        >
          <div className="grid gap-3">
            <div>
              <h3 className="text-sm font-bold text-slate-800">
                {tx("Trainer")}
              </h3>
              <p className="mt-1 text-xs leading-5 text-slate-500">
                {" "}
                {tx("The session is booked with an active trainer.")}{" "}
              </p>
            </div>
            {trainers.pending ? (
              <p className="text-sm text-slate-500" role="status">
                {" "}
                {tx("Loading trainers.")}{" "}
              </p>
            ) : null}
            <Alert message={trainers.error} />
            {!trainers.pending &&
            !trainers.error &&
            trainers.items.length === 0 ? (
              <p className="text-sm text-slate-500">
                {" "}
                {tx("No trainers are available.")}{" "}
              </p>
            ) : null}
            <Field label="Trainer">
              <SelectInput name="trainer_staff_profile_id" required>
                <option value="">{tx("Select a trainer")}</option>
                {trainers.items.map((trainer) => (
                  <option key={trainer.id} value={trainer.id}>
                    {trainer.display_name} ({trainer.staff_code})
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
              <h3 className="text-sm font-bold text-slate-800">{tx("Time")}</h3>
              <p className="mt-1 text-xs leading-5 text-slate-500">
                {" "}
                {tx("Enter the start and end in the gym's local time.")}{" "}
              </p>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Starts">
                <TextInput name="starts_at" required type="datetime-local" />
              </Field>
              <Field label="Ends">
                <TextInput name="ends_at" required type="datetime-local" />
              </Field>
            </div>
          </div>
          <div className="grid gap-3 border-t border-slate-100 pt-5">
            <div>
              <h3 className="text-sm font-bold text-slate-800">
                {tx("Package")}
              </h3>
              <p className="mt-1 text-xs leading-5 text-slate-500">
                {canUnmetered
                  ? tx(
                      "Use an active PT package, or record an unmetered manager reason.",
                    )
                  : tx(
                      "Attendance and no-show consume one session from this package.",
                    )}
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
                      "No active PT packages are available for this member.",
                    )}{" "}
                  </p>
                ) : null}
                <Field label="Member package">
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
        </BookingDialog>
      ) : null}

      {cancelling && canManage ? (
        <BookingDialog
          description="The reason is optional. Only the text entered here is sent."
          descriptionId="cancel-pt-booking-description"
          dialogId="cancel-pt-booking-dialog"
          dialogRef={dialogRef}
          error={dialogError}
          onClose={closeDialog}
          onSubmit={submitCancel}
          pending={pending}
          submitClass="button-cancel"
          submitLabel="Confirm booking cancellation"
          title={tx("Cancel PT booking")}
          titleId="cancel-pt-booking-title"
        >
          <p className="text-sm text-slate-600">
            <time dateTime={cancelling.starts_at}>
              {formatInstant(cancelling.starts_at, timeZone)}
            </time>
            <span aria-hidden="true"> – </span>
            <time dateTime={cancelling.ends_at}>
              {formatInstant(cancelling.ends_at, timeZone)}
            </time>
          </p>
          <p className="text-sm text-slate-600">
            {" "}
            {tx("Member")}{" "}
            {cancelling.member_name || (
              <IdentifierValue value={cancelling.member_id} />
            )}
          </p>
          <ReasonField label="Cancellation reason" required={false} />
        </BookingDialog>
      ) : null}
    </div>
  );
}
