"use client";
import { useTx } from "@/i18n/use-tx";

import { useEffect, useState, type FormEvent } from "react";
import QRCode from "qrcode";

import { FormDialog } from "@/components/form-dialog";
import {
  OperationError,
  issueCheckInChallenge,
} from "@/features/operations/client";
import type { CheckInChallengeIssued } from "@/types/api";

const ROTATION_LEAD_MS = 5_000;

export function nextChallengeRotationMs(
  expiresAt: string,
  now = Date.now(),
): number {
  const expiry = new Date(expiresAt).getTime();
  if (Number.isNaN(expiry)) return 0;
  return Math.max(0, expiry - now - ROTATION_LEAD_MS);
}

function failureMessage(caught: unknown): string {
  if (caught instanceof OperationError) return caught.message;
  if (caught instanceof Error && caught.message) return caught.message;
  return "The check-in code could not be issued.";
}

type RotationPhase = "Live" | "Refreshing" | "Preparing" | "Unavailable";

function rotationPhase(input: {
  failed: boolean;
  ready: boolean;
  pending: boolean;
  rotating: boolean;
}): RotationPhase {
  if (input.failed) return "Unavailable";
  if (!input.ready) return "Preparing";
  if (input.pending || input.rotating) return "Refreshing";
  return "Live";
}

function rotationTone(phase: RotationPhase): string {
  if (phase === "Live") {
    return "bg-emerald-50 text-emerald-800 ring-emerald-600/20";
  }
  if (phase === "Unavailable") {
    return "bg-rose-50 text-rose-800 ring-rose-600/20";
  }
  return "bg-amber-50 text-amber-900 ring-amber-600/20";
}

function rotationDot(phase: RotationPhase): string {
  if (phase === "Live") return "bg-emerald-500";
  if (phase === "Unavailable") return "bg-rose-500";
  return "bg-amber-500";
}

export function CheckInDisplay() {
  const tx = useTx();
  const [location, setLocation] = useState("Front desk");
  const [draft, setDraft] = useState("Front desk");
  const [editingLocation, setEditingLocation] = useState(false);
  const [qr, setQr] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(true);
  const [rotating, setRotating] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer = 0;

    async function rotate(): Promise<void> {
      if (cancelled) return;
      setRotating(true);
      try {
        const issued: CheckInChallengeIssued =
          await issueCheckInChallenge(location);
        if (cancelled) return;
        const image = await QRCode.toDataURL(issued.payload, {
          errorCorrectionLevel: "M",
          margin: 1,
          width: 512,
        });
        if (cancelled) return;
        setQr(image);
        setExpiresAt(issued.expires_at);
        setError(null);
        setPending(false);
        setRotating(false);
        timer = window.setTimeout(
          () => void rotate(),
          nextChallengeRotationMs(issued.expires_at),
        );
      } catch (caught) {
        if (cancelled) return;
        setError(failureMessage(caught));
        setPending(false);
        setRotating(false);
        timer = window.setTimeout(() => void rotate(), ROTATION_LEAD_MS);
      }
    }

    void rotate();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      setQr(null);
      setExpiresAt(null);
    };
  }, [location]);

  function saveLocation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setLocation(draft);
    setEditingLocation(false);
  }

  const phase = rotationPhase({
    failed: Boolean(error),
    pending,
    ready: Boolean(qr),
    rotating,
  });

  return (
    <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_20rem]">
      {error ? (
        <p className="error xl:col-span-2" role="alert">
          {tx(error)}
        </p>
      ) : null}
      <section
        aria-labelledby="qr-title"
        className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
      >
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4 sm:px-8">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.16em] text-amber-700">
              {" "}
              {tx("Member scan")}{" "}
            </p>
            <h2
              className="mt-1 text-2xl font-black tracking-tight text-slate-900"
              id="qr-title"
            >
              {" "}
              {tx("Onsite check-in code")}{" "}
            </h2>
          </div>
          <p
            className={`inline-flex shrink-0 items-center gap-2 rounded-full px-3 py-1 text-xs font-bold ring-1 ring-inset ${rotationTone(phase)}`}
          >
            <span
              aria-hidden="true"
              className={`size-2 rounded-full ${rotationDot(phase)} ${
                phase === "Live" ? "" : "animate-pulse"
              }`}
            />
            {phase}
          </p>
        </div>
        <div className="bg-slate-950 px-4 py-8 sm:px-8 sm:py-10">
          <div className="mx-auto w-full max-w-lg rounded-2xl bg-white p-4 shadow-xl shadow-black/20 sm:p-6">
            {qr ? (
              <img
                alt={tx("Current onsite check-in code")}
                className="mx-auto h-auto w-full"
                src={qr}
              />
            ) : (
              <div
                className="grid aspect-square min-h-64 place-items-center px-4 text-center text-sm font-semibold text-slate-500"
                role="status"
              >
                {" "}
                {tx("Preparing the code")}{" "}
              </div>
            )}
          </div>
          <div className="mx-auto mt-6 max-w-lg text-center">
            <p className="text-sm leading-6 text-slate-300">
              {pending
                ? tx("Issuing a new code.")
                : tx(
                    "This code rotates automatically and is kept only on this screen.",
                  )}
            </p>
            {expiresAt ? (
              <p className="mt-3 text-base font-semibold text-white sm:text-lg">
                {" "}
                {tx("Refreshes before")}{" "}
                <time dateTime={expiresAt}>
                  {new Date(expiresAt).toLocaleTimeString()}
                </time>
              </p>
            ) : null}
          </div>
        </div>
      </section>
      <section className="mx-auto grid w-full max-w-lg content-start gap-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm xl:mx-0 xl:max-w-none">
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.16em] text-amber-700">
            {" "}
            {tx("Location")}{" "}
          </p>
          <p className="mt-2 text-xs font-medium text-slate-500">
            {" "}
            {tx("Current location")}{" "}
          </p>
          <p className="mt-1 text-2xl font-black tracking-tight text-slate-900">
            {location}
          </p>
        </div>
        <p className="text-sm leading-6 text-slate-500">
          {" "}
          {tx(
            "Each new code uses this location. The code itself stays on this screen.",
          )}{" "}
        </p>
        <button
          className="button button-edit w-full"
          onClick={() => setEditingLocation(true)}
          type="button"
        >
          {" "}
          {tx("Update location")}{" "}
        </button>
      </section>
      {editingLocation ? (
        <FormDialog
          description="Each new code uses this location. The code itself stays on this screen."
          onClose={() => setEditingLocation(false)}
          onSubmit={saveLocation}
          title={tx("Display location")}
        >
          <label className="grid gap-2 text-sm font-semibold text-slate-800">
            {" "}
            {tx("Display location")}{" "}
            <input
              className="input"
              maxLength={80}
              onChange={(event) => setDraft(event.target.value)}
              required
              value={draft}
            />
          </label>
          <button className="button button-edit" type="submit">
            {" "}
            {tx("Update location")}{" "}
          </button>
        </FormDialog>
      ) : null}
    </div>
  );
}
