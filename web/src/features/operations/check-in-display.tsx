"use client";

import { useEffect, useState, type FormEvent } from "react";
import QRCode from "qrcode";

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

export function CheckInDisplay() {
  const [location, setLocation] = useState("Front desk");
  const [draft, setDraft] = useState("Front desk");
  const [qr, setQr] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(true);

  useEffect(() => {
    let cancelled = false;
    let timer = 0;

    async function rotate(): Promise<void> {
      try {
        const issued: CheckInChallengeIssued =
          await issueCheckInChallenge(location);
        if (cancelled) return;
        const image = await QRCode.toDataURL(issued.payload, {
          errorCorrectionLevel: "M",
          margin: 1,
          width: 280,
        });
        if (cancelled) return;
        setQr(image);
        setExpiresAt(issued.expires_at);
        setError(null);
        setPending(false);
        timer = window.setTimeout(
          () => void rotate(),
          nextChallengeRotationMs(issued.expires_at),
        );
      } catch (caught) {
        if (cancelled) return;
        setError(failureMessage(caught));
        setPending(false);
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
  }

  return (
    <div className="grid gap-4">
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
      <form className="card grid gap-3 p-5" onSubmit={saveLocation}>
        <label className="grid gap-1">
          Display location
          <input
            className="input"
            maxLength={80}
            onChange={(event) => setDraft(event.target.value)}
            required
            value={draft}
          />
        </label>
        <button className="button" type="submit">
          Update location
        </button>
      </form>
      <section aria-labelledby="qr-title" className="card p-5">
        <h2 className="font-bold" id="qr-title">
          Onsite check-in code
        </h2>
        <p className="muted mt-2">
          {pending
            ? "Issuing a new code."
            : "This code rotates automatically and is kept only on this screen."}
        </p>
        {qr ? (
          <img
            alt="Current onsite check-in code"
            className="mt-4 h-72 w-72"
            src={qr}
          />
        ) : null}
        {expiresAt ? (
          <p className="muted mt-3">
            Refreshes before {new Date(expiresAt).toLocaleTimeString()}
          </p>
        ) : null}
      </section>
    </div>
  );
}
