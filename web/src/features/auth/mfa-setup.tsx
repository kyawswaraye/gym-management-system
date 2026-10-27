"use client";
import { useTx } from "@/i18n/use-tx";

import Image from "next/image";
import { useId, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import QRCode from "qrcode";

import { PasswordInput } from "@/features/auth/password-input";
import { platformMutation } from "@/features/platform/client";
import { DigitInput } from "@/lib/ui/digit-input";

type Enrollment = {
  enrollment_token: string;
  secret: string;
  otpauth_uri: string;
  expires_at: string;
};

export function MFASetup() {
  const tx = useTx();
  const router = useRouter();
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);
  const passwordId = useId();
  const [qr, setQr] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function begin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setPending(true);
    const values = new FormData(event.currentTarget);
    try {
      const result = await platformMutation<Enrollment>(
        "auth/mfa/enroll",
        "POST",
        {
          current_password: String(values.get("current_password") ?? ""),
        },
      );
      setEnrollment(result);
      setQr(
        await QRCode.toDataURL(result.otpauth_uri, { width: 240, margin: 1 }),
      );
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "Unable to begin MFA setup.",
      );
    } finally {
      setPending(false);
    }
  }

  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!enrollment) return;
    setError(null);
    setPending(true);
    const values = new FormData(event.currentTarget);
    try {
      await platformMutation("auth/mfa/enroll/confirm", "POST", {
        enrollment_token: enrollment.enrollment_token,
        code: String(values.get("code") ?? ""),
      });
      router.back();
      router.refresh();
    } catch (failure) {
      setError(
        failure instanceof Error ? failure.message : "Unable to confirm MFA.",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="card max-w-xl" aria-labelledby="mfa-title">
      <h2 id="mfa-title" className="text-xl font-bold">
        {" "}
        {tx("Authenticator setup")}{" "}
      </h2>
      <p className="muted mt-2 text-sm">
        {" "}
        {tx(
          "Owner changes require a recent code from your authenticator app.",
        )}{" "}
      </p>
      {error && (
        <p className="error mt-4" role="alert">
          {tx(error)}
        </p>
      )}
      {!enrollment ? (
        <form className="mt-5 grid gap-4" onSubmit={begin}>
          <div className="field">
            <label htmlFor={passwordId}>{tx("Current password")}</label>
            <PasswordInput id={passwordId} name="current_password" required />
          </div>
          <button className="button" disabled={pending}>
            {pending ? tx("Preparing…") : tx("Set up authenticator")}
          </button>
        </form>
      ) : (
        <div className="mt-5 grid gap-4">
          {qr && (
            <Image
              src={qr}
              width={240}
              height={240}
              unoptimized
              alt={tx("Authenticator setup QR code")}
            />
          )}
          <p className="text-sm">
            {" "}
            {tx("Manual key:")}{" "}
            <code className="break-all font-mono">{enrollment.secret}</code>
          </p>
          <form className="grid gap-4" onSubmit={confirm}>
            <label className="field">
              <span>{tx("Six-digit code")}</span>
              <DigitInput
                className="input"
                maxLength={6}
                mode={{ kind: "code" }}
                name="code"
                pattern="[0-9]{6}"
                required
              />
            </label>
            <button className="button" disabled={pending}>
              {pending ? tx("Confirming…") : tx("Confirm MFA")}
            </button>
          </form>
        </div>
      )}
    </section>
  );
}
