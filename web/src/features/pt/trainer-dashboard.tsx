"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  getTrainerDashboard,
  getTrainerSessionReport,
} from "@/features/pt/client";
import type {
  DashboardRangePreset,
  TrainerDashboard,
  TrainerDashboardQuery,
  TrainerSessionCounts,
  TrainerSessionReport,
} from "@/types/api";

const PRESETS: { value: DashboardRangePreset; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "this_week", label: "This week" },
  { value: "this_month", label: "This month" },
  { value: "last_month", label: "Last month" },
  { value: "this_year", label: "This year" },
];

const SESSION_FIELDS: { key: keyof TrainerSessionCounts; label: string }[] = [
  { key: "bookings", label: "Bookings" },
  { key: "unique_members", label: "Unique members" },
  { key: "booked_sessions", label: "Booked sessions" },
  { key: "attended_sessions", label: "Attended sessions" },
  { key: "no_show_sessions", label: "No-show sessions" },
  { key: "cancelled_sessions", label: "Cancelled sessions" },
];

export function TrainerDashboardManager() {
  const [preset, setPreset] = useState<DashboardRangePreset>("today");
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const [query, setQuery] = useState<TrainerDashboardQuery>({ range: "today" });
  const [dashboard, setDashboard] = useState<TrainerDashboard | null>(null);
  const [error, setError] = useState("");
  const [reportFrom, setReportFrom] = useState("");
  const [reportTo, setReportTo] = useState("");
  const [report, setReport] = useState<TrainerSessionReport | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportError, setReportError] = useState("");
  const dashboardRequest = useRef(0);
  const reportRequest = useRef(0);

  const load = useCallback(async () => {
    const requestId = dashboardRequest.current + 1;
    dashboardRequest.current = requestId;
    setError("");
    try {
      const next = await getTrainerDashboard(query);
      if (dashboardRequest.current !== requestId) return;
      setDashboard(next);
      setError("");
    } catch (caught) {
      if (dashboardRequest.current !== requestId) return;
      setDashboard(null);
      setError(
        caught instanceof Error
          ? caught.message
          : "Unable to load the trainer dashboard.",
      );
    }
  }, [query]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  function selectPreset(value: DashboardRangePreset) {
    setPreset(value);
    setQuery((current) =>
      "range" in current && current.range === value
        ? current
        : { range: value },
    );
  }

  function applyCustomRange() {
    if (!customFrom || !customTo) return;
    setQuery({ date_from: customFrom, date_to: customTo });
  }

  async function showSessionReport() {
    if (!reportFrom || !reportTo) return;
    const requestId = reportRequest.current + 1;
    reportRequest.current = requestId;
    setReportLoading(true);
    setReportError("");
    setReport(null);
    try {
      const next = await getTrainerSessionReport({
        date_from: reportFrom,
        date_to: reportTo,
      });
      if (reportRequest.current !== requestId) return;
      setReport(next);
    } catch (caught) {
      if (reportRequest.current !== requestId) return;
      setReport(null);
      setReportError(
        caught instanceof Error
          ? caught.message
          : "Unable to load the session report.",
      );
    } finally {
      if (reportRequest.current === requestId) setReportLoading(false);
    }
  }

  const presetQuery = "range" in query ? query.range : "";

  return (
    <div className="grid gap-6">
      <div
        aria-label="Dashboard range"
        className="flex flex-wrap gap-2"
        role="group"
      >
        {PRESETS.map((item) => (
          <button
            aria-pressed={presetQuery === item.value}
            className={
              preset === item.value && presetQuery === item.value
                ? "button"
                : "rounded-md border px-4 py-2"
            }
            key={item.value}
            onClick={() => selectPreset(item.value)}
            type="button"
          >
            {item.label}
          </button>
        ))}
      </div>
      <form
        aria-label="Custom dashboard range"
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          applyCustomRange();
        }}
      >
        <label className="grid gap-1 text-sm">
          From
          <input
            className="rounded-md border px-3 py-2"
            onChange={(event) => setCustomFrom(event.target.value)}
            type="date"
            value={customFrom}
          />
        </label>
        <label className="grid gap-1 text-sm">
          To
          <input
            className="rounded-md border px-3 py-2"
            onChange={(event) => setCustomTo(event.target.value)}
            type="date"
            value={customTo}
          />
        </label>
        <button
          className="button"
          disabled={!customFrom || !customTo}
          type="submit"
        >
          Apply custom range
        </button>
      </form>
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
      {!dashboard && !error ? (
        <p className="muted" role="status">
          Loading trainer dashboard…
        </p>
      ) : null}
      {dashboard ? (
        <>
          <p className="muted text-sm">
            {dashboard.today}. {dashboard.range.date_from} to{" "}
            {dashboard.range.date_to}. {dashboard.timezone}. Generated{" "}
            {dashboard.generated_at}.
          </p>
          <p className="text-sm">
            Trainer profile{" "}
            <span className="font-mono">
              {dashboard.trainer_staff_profile_id}
            </span>
          </p>
          <section
            aria-labelledby="trainer-summary-title"
            className="grid gap-4"
          >
            <h2 className="text-lg font-bold" id="trainer-summary-title">
              Session summary
            </h2>
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <Metric
                label="Active assigned members"
                value={String(dashboard.active_assigned_members)}
              />
              {SESSION_FIELDS.map((field) => (
                <Metric
                  key={field.key}
                  label={field.label}
                  value={String(dashboard.sessions[field.key])}
                />
              ))}
            </div>
          </section>
          <section
            aria-labelledby="upcoming-sessions-title"
            className="grid gap-4"
          >
            <h2 className="text-lg font-bold" id="upcoming-sessions-title">
              Upcoming sessions
            </h2>
            {dashboard.upcoming_bookings.length === 0 ? (
              <p className="muted" role="status">
                No upcoming sessions.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full text-left text-sm">
                  <caption className="sr-only">Upcoming sessions</caption>
                  <thead>
                    <tr>
                      <th className="px-2 py-1" scope="col">
                        Member
                      </th>
                      <th className="px-2 py-1" scope="col">
                        Member number
                      </th>
                      <th className="px-2 py-1" scope="col">
                        Starts
                      </th>
                      <th className="px-2 py-1" scope="col">
                        Ends
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {dashboard.upcoming_bookings.map((booking) => (
                      <tr key={booking.id}>
                        <th className="px-2 py-1" scope="row">
                          {booking.full_name}
                        </th>
                        <td className="px-2 py-1">{booking.member_number}</td>
                        <td className="px-2 py-1">
                          <time dateTime={booking.starts_at}>
                            {booking.starts_at}
                          </time>
                        </td>
                        <td className="px-2 py-1">
                          <time dateTime={booking.ends_at}>
                            {booking.ends_at}
                          </time>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      ) : null}
      <section
        aria-labelledby="trainer-session-report-title"
        className="grid gap-4"
      >
        <h2 className="text-lg font-bold" id="trainer-session-report-title">
          Session report
        </h2>
        <form
          aria-label="Session report range"
          className="flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void showSessionReport();
          }}
        >
          <label className="grid gap-1 text-sm">
            From date
            <input
              className="rounded-md border px-3 py-2"
              onChange={(event) => setReportFrom(event.target.value)}
              required
              type="date"
              value={reportFrom}
            />
          </label>
          <label className="grid gap-1 text-sm">
            To date
            <input
              className="rounded-md border px-3 py-2"
              onChange={(event) => setReportTo(event.target.value)}
              required
              type="date"
              value={reportTo}
            />
          </label>
          <button
            className="button"
            disabled={!reportFrom || !reportTo || reportLoading}
            type="submit"
          >
            Show session report
          </button>
        </form>
        {reportError ? (
          <p className="error" role="alert">
            {reportError}
          </p>
        ) : null}
        {reportLoading ? (
          <p className="muted" role="status">
            Loading session report…
          </p>
        ) : null}
        {!reportLoading && !reportError && !report ? (
          <p className="muted" role="status">
            Choose a date range to load your sessions.
          </p>
        ) : null}
        {report && !reportLoading ? (
          <SessionReportView report={report} />
        ) : null}
      </section>
    </div>
  );
}

function SessionReportView({ report }: { report: TrainerSessionReport }) {
  return (
    <div className="grid gap-4">
      <p>{report.gym.name}</p>
      <p>{report.gym.code}</p>
      <p>
        {report.range.date_from} to {report.range.date_to}
      </p>
      <p>{report.timezone}</p>
      <p>{report.generated_at}</p>
      <p>
        <span className="sr-only">Source status </span>
        {report.source_status_policy}
      </p>
      {report.rows.length === 0 ? (
        <p className="muted" role="status">
          No session days in this range.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full text-left text-sm">
            <caption className="sr-only">Session report rows</caption>
            <thead>
              <tr>
                <th className="px-2 py-1" scope="col">
                  Date
                </th>
                {SESSION_FIELDS.map((field) => (
                  <th className="px-2 py-1" key={field.key} scope="col">
                    {field.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {report.rows.map((row) => (
                <tr key={row.date}>
                  <th className="px-2 py-1" scope="row">
                    {row.date}
                  </th>
                  {SESSION_FIELDS.map((field) => (
                    <td className="px-2 py-1" key={field.key}>
                      {row[field.key]}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <section aria-labelledby="trainer-session-totals">
        <h3 className="text-lg font-semibold" id="trainer-session-totals">
          Totals
        </h3>
        <dl className="mt-3 grid gap-2">
          {SESSION_FIELDS.map((field) => (
            <div key={field.key}>
              <dt>{field.label}</dt>
              <dd>{report.totals[field.key]}</dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <article className="card p-5">
      <p className="muted text-sm">{label}</p>
      <p className="mt-2 text-2xl font-bold">{value}</p>
    </article>
  );
}
