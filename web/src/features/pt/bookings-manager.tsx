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
import { zonedLocalToIso } from "@/lib/date";
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

function shiftDate(iso: string, days: number): string {
  const [year, month, day] = iso.split("-").map(Number);
  const date = new Date(Date.UTC(year ?? 0, (month ?? 1) - 1, day ?? 1));
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
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
  const [dateFrom, setDateFrom] = useState(today);
  const [dateTo, setDateTo] = useState(shiftDate(today, 6));
  const [memberFilter, setMemberFilter] = useState<PickedMember | null>(null);
  const [member, setMember] = useState<PickedMember | null>(null);
  const [packages, setPackages] = useState<MemberServicePackage[]>([]);
  const [packageNotice, setPackageNotice] = useState("");
  const [cancelling, setCancelling] = useState<PTBooking | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [creating, setCreating] = useState(false);
  const [unmetered, setUnmetered] = useState(false);
  const key = useRef<{ fingerprint: string; value: string } | null>(null);
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
      bookings.reload();
      return true;
    } catch (caught) {
      setError(phase5Error(caught, "The booking command failed."));
      return false;
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="grid gap-4">
      <Alert message={error || bookings.error} />
      {notice ? (
        <p aria-live="polite" role="status">
          {notice}
        </p>
      ) : null}
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
          Show calendar
        </button>
      </form>
      <p className="muted">Calendar range is at most 42 gym-local days.</p>
      <MemberSearch
        action="Search booking members"
        label="Filter bookings by member"
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
      {bookings.pending ? <Loading label="Loading bookings." /> : null}
      {!bookings.pending && bookings.rows.length === 0 ? (
        <Empty label="No PT bookings are in this range." />
      ) : (
        <ul className="grid gap-3">
          {bookings.rows.map((row) => (
            <li className="rounded border border-slate-200 p-3" key={row.id}>
              <p>
                {row.starts_at}–{row.ends_at} <StatusText status={row.status} />
              </p>
              <p className="muted">
                Member {row.member_id} · trainer {row.trainer_staff_profile_id}{" "}
                ·{" "}
                {row.unmetered
                  ? `Unmetered · ${row.unmetered_reason}`
                  : `Package ${row.member_service_package_id ?? "missing"}`}
              </p>
              {canManage && row.status === "BOOKED" ? (
                <div className="mt-2 flex flex-wrap gap-3">
                  <button
                    className="underline"
                    disabled={pending}
                    onClick={() =>
                      void command(
                        () =>
                          attendPtBooking(
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
                          noShowPtBooking(
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
                  <button
                    className="underline"
                    disabled={pending}
                    onClick={() => setCancelling(row)}
                    type="button"
                  >
                    Cancel booking
                  </button>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      <CursorControls
        bounded={bookings.bounded}
        hasMore={bookings.hasMore}
        index={bookings.index}
        onNext={() => void bookings.goNext()}
        onPrevious={() => void bookings.goPrevious()}
        pending={bookings.pending}
      />
      {cancelling && canManage ? (
        <form
          aria-labelledby="cancel-pt-booking-title"
          className="grid gap-3 rounded border border-slate-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            const reason = String(
              new FormData(event.currentTarget).get("reason") ?? "",
            );
            void command(
              () =>
                cancelPtBooking(
                  cancelling.id,
                  { reason },
                  commandKey(`cancel:${cancelling.id}:${reason}`),
                ),
              "Booking cancelled.",
            ).then((saved) => {
              if (saved) setCancelling(null);
            });
          }}
          role="dialog"
        >
          <h3 className="font-semibold" id="cancel-pt-booking-title">
            Cancel PT booking
          </h3>
          <p className="muted">
            The reason is optional. Only the text entered here is sent.
          </p>
          <ReasonField label="Cancellation reason" required={false} />
          <button className="button" disabled={pending} type="submit">
            Confirm booking cancellation
          </button>
        </form>
      ) : null}
      {canManage ? (
        <button
          className="button"
          onClick={() => setCreating(true)}
          type="button"
        >
          Book session
        </button>
      ) : null}
      {creating && canManage ? (
        <form
          aria-labelledby="book-pt-title"
          className="grid gap-3 rounded border border-slate-200 p-4"
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
              trainer_staff_profile_id: String(
                form.get("trainer_staff_profile_id") ?? "",
              ),
              member_id: member.id,
              starts_at: zonedLocalToIso(
                String(form.get("starts_at") ?? ""),
                timeZone,
              ),
              ends_at: zonedLocalToIso(
                String(form.get("ends_at") ?? ""),
                timeZone,
              ),
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
              () => createPtBooking(body, commandKey(JSON.stringify(body))),
              "Session booked.",
            ).then((saved) => {
              if (saved) setCreating(false);
            });
          }}
          role="dialog"
        >
          <h3 className="font-semibold" id="book-pt-title">
            Book session
          </h3>
          <Alert message={trainers.error || packageNotice} />
          <Field label="Trainer">
            <SelectInput name="trainer_staff_profile_id" required>
              <option value="">Select a trainer</option>
              {trainers.items.map((trainer) => (
                <option key={trainer.id} value={trainer.id}>
                  {trainer.display_name} ({trainer.staff_code})
                </option>
              ))}
            </SelectInput>
          </Field>
          <MemberSearch
            disabled={pending}
            onSelect={(picked) => {
              setMember(picked);
              setPackageNotice("");
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
          <Field label="Starts">
            <TextInput name="starts_at" required type="datetime-local" />
          </Field>
          <Field label="Ends">
            <TextInput name="ends_at" required type="datetime-local" />
          </Field>
          {canUnmetered ? (
            <label className="flex items-center gap-2">
              <input
                checked={unmetered}
                onChange={(event) => setUnmetered(event.target.checked)}
                type="checkbox"
              />
              Unmetered manager booking
            </label>
          ) : null}
          {canUnmetered && unmetered ? (
            <ReasonField label="Unmetered reason" name="unmetered_reason" />
          ) : (
            <Field label="Member package">
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
            Save booking
          </button>
        </form>
      ) : null}
    </div>
  );
}
