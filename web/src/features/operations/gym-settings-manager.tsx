"use client";
import { useTx } from "@/i18n/use-tx";

import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type ReactNode,
} from "react";

import { FormDialog } from "@/components/form-dialog";
import { GymLogo } from "@/components/gym-logo";
import {
  deleteGymLogo,
  getGymProfile,
  getGymSettings,
  updateGymProfile,
  updateGymSettings,
  uploadGymLogo,
} from "@/features/operations/client";
import { assignedMemberNumber } from "@/features/operations/member-profile";
import type { GymSettingsAccess } from "@/lib/auth/authorization";
import { IdentifierValue } from "@/lib/ui/identifier";
import type {
  GymProfile,
  GymProfileUpdate,
  GymSettings,
  GymSettingsUpdate,
} from "@/types/api";

const WEEKDAYS = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
] as const;

const PROFILE_LIMITS = {
  name: 150,
  phone: 32,
  address_line1: 255,
  address_line2: 255,
  city: 100,
  region: 100,
  postal_code: 20,
  timezone: 64,
} as const;

type ProfileTextField = keyof typeof PROFILE_LIMITS;

const PROFILE_FIELDS: {
  name: ProfileTextField;
  label: string;
  group: "identity" | "contact" | "address" | "locale";
  autoComplete?: string;
}[] = [
  {
    name: "name",
    label: "Name",
    group: "identity",
    autoComplete: "organization",
  },
  { name: "phone", label: "Phone", group: "contact", autoComplete: "tel" },
  {
    name: "address_line1",
    label: "Address line 1",
    group: "address",
    autoComplete: "address-line1",
  },
  {
    name: "address_line2",
    label: "Address line 2",
    group: "address",
    autoComplete: "address-line2",
  },
  {
    name: "city",
    label: "City",
    group: "address",
    autoComplete: "address-level2",
  },
  {
    name: "region",
    label: "Region",
    group: "address",
    autoComplete: "address-level1",
  },
  {
    name: "postal_code",
    label: "Postal code",
    group: "address",
    autoComplete: "postal-code",
  },
  { name: "timezone", label: "Timezone", group: "locale" },
];

const INTEGER_FIELDS = [
  {
    name: "check_in_duplicate_window_seconds",
    label: "Check-in duplicate window (seconds)",
    min: 0,
    max: 86_400,
  },
  {
    name: "check_in_challenge_ttl_seconds",
    label: "Check-in challenge lifetime (seconds)",
    min: 30,
    max: 600,
  },
  {
    name: "check_in_challenge_max_uses",
    label: "Check-in challenge maximum uses",
    min: 1,
    max: 100,
  },
  {
    name: "class_cancellation_cutoff_minutes",
    label: "Class cancellation cutoff (minutes)",
    min: 0,
    max: 2_147_483_647,
  },
] as const;

const BOOLEAN_FIELDS = [
  {
    name: "allow_manual_member_numbers",
    label: "Allow manual member numbers",
  },
  {
    name: "membership_expiry_email_enabled",
    label: "Email membership expiry warnings",
  },
  {
    name: "membership_expiry_push_enabled",
    label: "Push membership expiry warnings",
  },
  {
    name: "class_no_show_consumes_session",
    label: "A class no-show consumes a session",
  },
] as const;

function failureMessage(failure: unknown, fallback: string): string {
  return failure instanceof Error ? failure.message : fallback;
}

type Tx = (text: string, vars?: Record<string, string | number>) => string;

function limitedText(
  form: FormData,
  field: ProfileTextField,
  label: string,
  tx: Tx,
  required = false,
): string {
  const value = String(form.get(field) ?? "").trim();
  const max = PROFILE_LIMITS[field];
  if (required && value.length === 0) {
    throw new Error(tx("Enter a {label}.", { label: label.toLowerCase() }));
  }
  if (value.length > max) {
    throw new Error(
      tx("Enter a {label} up to {max} characters.", {
        label: label.toLowerCase(),
        max,
      }),
    );
  }
  return value;
}

function readProfile(form: FormData, tx: Tx): GymProfileUpdate {
  const email = String(form.get("email") ?? "").trim();
  if (
    email.length > 254 ||
    (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email))
  ) {
    throw new Error("Enter a valid email address or leave it blank.");
  }
  const countryCode = String(form.get("country_code") ?? "")
    .trim()
    .toUpperCase();
  if (countryCode && !/^[A-Z]{2}$/.test(countryCode)) {
    throw new Error("Enter a two-letter country code or leave it blank.");
  }
  const currency = String(form.get("currency") ?? "")
    .trim()
    .toUpperCase();
  if (currency !== "THB" && currency !== "MMK") {
    throw new Error("Choose THB or MMK.");
  }
  const name = limitedText(form, "name", "Name", tx, true);
  return {
    name,
    legal_name: name,
    email,
    phone: limitedText(form, "phone", "Phone", tx),
    address_line1: limitedText(form, "address_line1", "Address line 1", tx),
    address_line2: limitedText(form, "address_line2", "Address line 2", tx),
    city: limitedText(form, "city", "City", tx),
    region: limitedText(form, "region", "Region", tx),
    postal_code: limitedText(form, "postal_code", "Postal code", tx),
    country_code: countryCode,
    timezone: limitedText(form, "timezone", "Timezone", tx, true),
    currency,
  };
}

function readInteger(
  form: FormData,
  name: string,
  label: string,
  min: number,
  max: number,
  tx: Tx,
): number {
  const text = String(form.get(name) ?? "").trim();
  if (!/^\d+$/.test(text)) {
    throw new Error(tx("Enter a whole number for {label}.", { label }));
  }
  const value = Number(text);
  if (value < min || value > max) {
    throw new Error(
      tx("Enter a value from {min} to {max} for {label}.", { min, max, label }),
    );
  }
  return value;
}

function readPrefix(
  form: FormData,
  name: string,
  label: string,
  tx: Tx,
): string {
  const prefix = String(form.get(name) ?? "")
    .trim()
    .toUpperCase();
  if (!/^[A-Z0-9-]{1,20}$/.test(prefix)) {
    throw new Error(
      tx("{label} must be 1 to 20 uppercase letters, digits, or hyphens.", {
        label,
      }),
    );
  }
  return prefix;
}

function readWarningDays(form: FormData, tx: Tx): number[] {
  const text = String(form.get("membership_expiry_warning_days") ?? "").trim();
  if (!text) return [];
  const parts = text
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
  if (parts.length > 366) {
    throw new Error(tx("Enter at most 366 warning days."));
  }
  const days = parts.map((part) => {
    if (!/^\d+$/.test(part) || Number(part) > 365) {
      throw new Error(
        tx("Each warning day must be a whole number from 0 to 365."),
      );
    }
    return Number(part);
  });
  if (new Set(days).size !== days.length) {
    throw new Error(tx("Warning days must be unique."));
  }
  return [...days].sort((left, right) => left - right);
}

function readSettings(form: FormData, tx: Tx): GymSettingsUpdate {
  return {
    week_starts_on: readInteger(
      form,
      "week_starts_on",
      "Week starts on",
      0,
      6,
      tx,
    ),
    allow_manual_member_numbers:
      form.get("allow_manual_member_numbers") === "on",
    member_number_prefix: readPrefix(
      form,
      "member_number_prefix",
      "Member number prefix",
      tx,
    ),
    receipt_number_prefix: readPrefix(
      form,
      "receipt_number_prefix",
      "Receipt number prefix",
      tx,
    ),
    check_in_duplicate_window_seconds: readInteger(
      form,
      "check_in_duplicate_window_seconds",
      "Check-in duplicate window (seconds)",
      0,
      86_400,
      tx,
    ),
    check_in_challenge_ttl_seconds: readInteger(
      form,
      "check_in_challenge_ttl_seconds",
      "Check-in challenge lifetime (seconds)",
      30,
      600,
      tx,
    ),
    check_in_challenge_max_uses: readInteger(
      form,
      "check_in_challenge_max_uses",
      "Check-in challenge maximum uses",
      1,
      100,
      tx,
    ),
    membership_expiry_warning_days: readWarningDays(form, tx),
    membership_expiry_email_enabled:
      form.get("membership_expiry_email_enabled") === "on",
    membership_expiry_push_enabled:
      form.get("membership_expiry_push_enabled") === "on",
    class_cancellation_cutoff_minutes: readInteger(
      form,
      "class_cancellation_cutoff_minutes",
      "Class cancellation cutoff (minutes)",
      0,
      2_147_483_647,
      tx,
    ),
    class_no_show_consumes_session:
      form.get("class_no_show_consumes_session") === "on",
    require_separate_finance_approval: true,
  };
}

const INTEGER_HINTS: Record<(typeof INTEGER_FIELDS)[number]["name"], string> = {
  check_in_duplicate_window_seconds:
    "A second scan inside this window is ignored.",
  check_in_challenge_ttl_seconds: "How long a check-in code stays valid.",
  check_in_challenge_max_uses: "How many times one code can be used.",
  class_cancellation_cutoff_minutes:
    "How close to the start a member can still cancel.",
};

const panelClass =
  "overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm";

function tabClass(selected: boolean): string {
  return selected
    ? "min-h-10 rounded-lg bg-white px-4 text-sm font-bold text-slate-900 shadow-sm"
    : "min-h-10 rounded-lg px-4 text-sm font-bold text-slate-500 hover:text-slate-800";
}

function receiptExample(prefix: string): string {
  return `${prefix}-${new Date().getFullYear()}-000001`;
}

function profileFields(group: (typeof PROFILE_FIELDS)[number]["group"]) {
  return PROFILE_FIELDS.filter((field) => field.group === group);
}

function Notice({
  error,
  success,
}: {
  error: string | null;
  success: string | null;
}) {
  const tx = useTx();
  if (!error && !success) return null;
  return (
    <>
      {error ? (
        <p className="error" role="alert">
          {tx(error)}
        </p>
      ) : null}
      {success ? (
        <p
          className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-900"
          role="status"
        >
          {tx(success)}
        </p>
      ) : null}
    </>
  );
}

function profileHasLogo(profile: GymProfile): boolean {
  return profile.has_logo;
}

function profileWhen(value: string | null | undefined): ReactNode {
  if (!value) return textValue("");
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return textValue(value);
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function Pill({ children, tone }: { children: ReactNode; tone: string }) {
  return (
    <span
      className={`inline-flex rounded-full px-2.5 py-1 text-xs font-bold ring-1 ring-inset ${tone}`}
    >
      {children}
    </span>
  );
}

function recordTone(status: string): string {
  switch (status) {
    case "ACTIVE":
      return "bg-emerald-50 text-emerald-700 ring-emerald-600/15";
    case "TRIAL":
      return "bg-sky-50 text-sky-700 ring-sky-600/15";
    case "PAST_DUE":
    case "SUSPENDED":
      return "bg-amber-50 text-amber-800 ring-amber-600/15";
    case "CANCELLED":
      return "bg-rose-50 text-rose-700 ring-rose-600/15";
    default:
      return "bg-slate-100 text-slate-600 ring-slate-500/10";
  }
}

function textValue(value: string | number | null | undefined): ReactNode {
  if (value === null || value === undefined || value === "") {
    return <span className="font-medium text-slate-400">—</span>;
  }
  if (typeof value === "number") return value;
  return <IdentifierValue value={value} />;
}

function Fact({
  label,
  children,
  wide = false,
}: {
  label: string;
  children: ReactNode;
  wide?: boolean;
}) {
  const tx = useTx();
  return (
    <div
      className={`rounded-xl bg-slate-50 px-3 py-2.5 ${wide ? "sm:col-span-2" : ""}`}
    >
      <dt className="text-[0.68rem] font-bold tracking-wide text-slate-500 uppercase">
        {tx(label)}
      </dt>
      <dd className="mt-1 text-sm font-semibold break-words text-slate-900">
        {children}
      </dd>
    </div>
  );
}

function ProfileBlock({
  title,
  hint,
  rows,
}: {
  title: string;
  hint?: string;
  rows: { label: string; value: ReactNode }[];
}) {
  const tx = useTx();
  return (
    <div>
      <h3 className="text-base font-bold text-[#050505]">{tx(title)}</h3>
      {hint ? <p className="mt-1 text-sm text-slate-500">{tx(hint)}</p> : null}
      <dl className="mt-3 border-t border-[#e4e6eb]">
        {rows.map((row) => (
          <div
            className="grid gap-1 border-b border-[#e4e6eb] py-3 sm:grid-cols-[12rem_minmax(0,1fr)] sm:items-baseline sm:gap-6"
            key={row.label}
          >
            <dt className="text-sm text-slate-500">{tx(row.label)}</dt>
            <dd className="min-w-0 text-sm font-semibold break-words text-[#050505]">
              {row.value}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function SettingGroup({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  const tx = useTx();
  return (
    <div>
      <h3 className="text-sm font-bold text-slate-900">{tx(title)}</h3>
      {hint ? (
        <p className="mt-0.5 text-sm text-slate-500">{tx(hint)}</p>
      ) : null}
      <dl className="mt-3 grid gap-2 sm:grid-cols-2">{children}</dl>
    </div>
  );
}

function Choice({ on }: { on: boolean }) {
  const tx = useTx();
  return (
    <Pill
      tone={
        on
          ? "bg-emerald-50 text-emerald-700 ring-emerald-600/15"
          : "bg-slate-100 text-slate-600 ring-slate-500/10"
      }
    >
      {on ? tx("Yes") : tx("No")}
    </Pill>
  );
}

function ReadOnlyRows({
  rows,
}: {
  rows: { label: string; value: ReactNode }[];
}) {
  const tx = useTx();
  return (
    <dl className="grid gap-2 sm:grid-cols-3">
      {rows.map((row) => (
        <div className="rounded-xl bg-slate-50 px-3 py-2.5" key={row.label}>
          <dt className="text-[0.68rem] font-bold tracking-wide text-slate-500 uppercase">
            {tx(row.label)}
          </dt>
          <dd className="mt-1 text-sm font-semibold text-slate-900">
            {row.value === "" ||
            row.value === null ||
            row.value === undefined ? (
              "—"
            ) : typeof row.value === "string" ? (
              <IdentifierValue value={row.value} />
            ) : (
              row.value
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function FormSection({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  const tx = useTx();
  return (
    <fieldset className="rounded-xl border border-slate-200 bg-slate-50/70 p-4">
      <legend className="px-1 text-sm font-bold text-slate-900">
        {tx(title)}
      </legend>
      {hint ? (
        <p className="mb-3 text-xs leading-5 text-slate-500">{tx(hint)}</p>
      ) : null}
      <div className="grid gap-4 sm:grid-cols-2">{children}</div>
    </fieldset>
  );
}

function TextControl({
  label,
  name,
  defaultValue,
  maxLength,
  required,
  type = "text",
  hint,
  autoComplete,
  className,
  min,
  max,
  step,
  spellCheck,
  uppercase = false,
}: {
  label: string;
  name: string;
  defaultValue: string | number;
  maxLength?: number;
  required?: boolean;
  type?: string;
  hint?: string;
  autoComplete?: string;
  className?: string;
  min?: number;
  max?: number;
  step?: number;
  spellCheck?: boolean;
  uppercase?: boolean;
}) {
  const tx = useTx();
  const hintId = hint ? `${name}-hint` : undefined;
  return (
    <div className={className}>
      <label className="field">
        <span className="text-sm font-semibold text-slate-700">
          {tx(label)}
        </span>
        <input
          aria-describedby={hintId}
          autoComplete={autoComplete}
          className={uppercase ? "input uppercase" : "input"}
          defaultValue={defaultValue}
          max={max}
          maxLength={maxLength}
          min={min}
          name={name}
          required={required}
          spellCheck={spellCheck}
          step={step}
          type={type}
        />
      </label>
      {hint ? (
        <p className="mt-1 text-xs leading-5 text-slate-500" id={hintId}>
          {tx(hint)}
        </p>
      ) : null}
    </div>
  );
}

function ToggleRow({
  name,
  label,
  defaultChecked,
  hint,
}: {
  name: string;
  label: string;
  defaultChecked: boolean;
  hint: string;
}) {
  const tx = useTx();
  const hintId = `${name}-hint`;
  return (
    <div className="sm:col-span-2">
      <label className="flex items-center justify-between gap-4 rounded-xl border border-slate-200 bg-white px-3 py-3">
        <span className="text-sm font-semibold text-slate-800">
          {tx(label)}
        </span>
        <input
          aria-describedby={hintId}
          className="size-4 shrink-0 accent-indigo-600"
          defaultChecked={defaultChecked}
          name={name}
          type="checkbox"
        />
      </label>
      <p className="mt-1 px-1 text-xs leading-5 text-slate-500" id={hintId}>
        {tx(hint)}
      </p>
    </div>
  );
}

function ProfileControl({
  profile,
  name,
  className,
  hint,
}: {
  profile: GymProfile;
  name: ProfileTextField;
  className?: string;
  hint?: string;
}) {
  const field = PROFILE_FIELDS.find((item) => item.name === name);
  if (!field) return null;
  return (
    <TextControl
      defaultValue={profile[name]}
      label={field.label}
      maxLength={PROFILE_LIMITS[name]}
      name={name}
      required={name === "name" || name === "timezone"}
      {...(field.autoComplete ? { autoComplete: field.autoComplete } : {})}
      {...(className ? { className } : {})}
      {...(hint ? { hint } : {})}
    />
  );
}

function SettingToggle({
  settings,
  name,
  hint,
}: {
  settings: GymSettings;
  name: (typeof BOOLEAN_FIELDS)[number]["name"];
  hint: string;
}) {
  const field = BOOLEAN_FIELDS.find((item) => item.name === name);
  if (!field) return null;
  return (
    <ToggleRow
      defaultChecked={settings[name]}
      hint={hint}
      label={field.label}
      name={name}
    />
  );
}

function IntegerControl({
  settings,
  name,
}: {
  settings: GymSettings;
  name: (typeof INTEGER_FIELDS)[number]["name"];
}) {
  const field = INTEGER_FIELDS.find((item) => item.name === name);
  if (!field) return null;
  return (
    <TextControl
      defaultValue={settings[name]}
      hint={INTEGER_HINTS[name]}
      label={field.label}
      max={field.max}
      min={field.min}
      name={name}
      required
      step={1}
      type="number"
    />
  );
}

function approvalCopy() {
  return "Separate finance approval is mandatory and cannot be turned off. Payments, expenses, retail sales, PT sales, and salary records must be approved by a different authorized manager or owner.";
}

function ImageFrameIcon() {
  return (
    <svg aria-hidden="true" className="size-7" fill="none" viewBox="0 0 24 24">
      <rect
        height="14"
        rx="2"
        stroke="currentColor"
        strokeWidth="1.6"
        width="18"
        x="3"
        y="5"
      />
      <path
        d="M7 15.5 10.2 12l2.1 2.2L15 11.5 17.5 15"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.6"
      />
      <circle cx="9" cy="9" fill="currentColor" r="1" />
    </svg>
  );
}

function UploadArrowIcon() {
  return (
    <svg aria-hidden="true" className="size-4" fill="none" viewBox="0 0 16 16">
      <path
        d="M8 12.5V3.5M8 3.5 4.5 7M8 3.5 11.5 7"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.6"
      />
    </svg>
  );
}

function RemoveIcon() {
  return (
    <svg aria-hidden="true" className="size-4" fill="none" viewBox="0 0 16 16">
      <path
        d="M4.5 4.5 11.5 11.5M11.5 4.5 4.5 11.5"
        stroke="currentColor"
        strokeLinecap="round"
        strokeWidth="1.6"
      />
    </svg>
  );
}

function LogoFileInput({
  pending,
  onFile,
}: {
  pending: boolean;
  onFile: (event: ChangeEvent<HTMLInputElement>) => void;
}) {
  return (
    <input
      accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp"
      className="absolute inset-0 z-10 cursor-pointer opacity-0 disabled:cursor-not-allowed"
      disabled={pending}
      type="file"
      onChange={onFile}
    />
  );
}

function GymLogoPicker({
  hasLogo,
  canUpdate,
  pending,
  previewUrl,
  onFile,
  onRemove,
}: {
  hasLogo: boolean;
  canUpdate: boolean;
  pending: boolean;
  previewUrl: string | null;
  onFile: (event: ChangeEvent<HTMLInputElement>) => void;
  onRemove: () => void;
}) {
  const tx = useTx();
  if (!canUpdate && !hasLogo && !previewUrl) return null;
  const preview = previewUrl ? (
    <img
      alt=""
      className="max-h-40 max-w-full rounded-md object-contain"
      src={previewUrl}
    />
  ) : hasLogo ? (
    <GymLogo
      className="max-h-40 max-w-full rounded-md object-contain"
      fallback={
        <span className="h-28 w-28 animate-pulse rounded-md bg-white" />
      }
    />
  ) : null;
  return (
    <div>
      <div className="flex items-center gap-3">
        <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-[#e7f3ff] text-[#0866ff]">
          <ImageFrameIcon />
        </span>
        <div>
          <p className="text-sm font-bold text-[#050505]">{tx("Gym logo")}</p>
          <p className="text-sm text-slate-500">
            {tx("Max 1 MB · PNG, JPEG, WebP.")}
          </p>
        </div>
      </div>
      <p className="mt-4 text-sm font-semibold text-slate-500">{tx("Image")}</p>
      {preview ? (
        <div className="mt-2">
          <div className="grid min-h-40 place-items-center rounded-xl bg-[#f0f2f5] p-4">
            {preview}
          </div>
          {canUpdate ? (
            <div className="mt-3 grid grid-cols-2 gap-2">
              <label
                className={`button button-sm relative w-full ${pending ? "pointer-events-none opacity-55" : ""}`}
              >
                <UploadArrowIcon /> {tx("Change")}{" "}
                <LogoFileInput pending={pending} onFile={onFile} />
              </label>
              <button
                className="button button-delete button-sm w-full"
                disabled={pending}
                type="button"
                onClick={onRemove}
              >
                <RemoveIcon /> {tx("Remove")}{" "}
              </button>
            </div>
          ) : null}
        </div>
      ) : (
        <label
          className={`relative mt-2 flex min-h-40 w-full cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-[#c5cad3] bg-[#f0f2f5] px-4 py-6 text-sm font-semibold text-slate-500 hover:border-[#0866ff] hover:text-[#0866ff] ${pending ? "pointer-events-none opacity-55" : ""}`}
        >
          <ImageFrameIcon /> {tx("Click to select an image")}{" "}
          <LogoFileInput pending={pending} onFile={onFile} />
        </label>
      )}
    </div>
  );
}

function ProfileSection({
  canUpdate,
  pending,
  onPending,
}: {
  canUpdate: boolean;
  pending: boolean;
  onPending: (value: boolean) => void;
}) {
  const tx = useTx();
  const [profile, setProfile] = useState<GymProfile | null>(null);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const previewUrlRef = useRef<string | null>(null);

  useEffect(() => {
    let active = true;
    void getGymProfile()
      .then((value) => {
        if (active) setProfile(value);
      })
      .catch((failure: unknown) => {
        if (active)
          setError(failureMessage(failure, "Unable to load the gym profile."));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSuccess(null);
    const form = new FormData(event.currentTarget);
    let payload: GymProfileUpdate;
    try {
      payload = readProfile(form, tx);
    } catch (failure) {
      setError(failureMessage(failure, "Check the gym profile and try again."));
      return;
    }
    onPending(true);
    setError(null);
    try {
      setProfile(await updateGymProfile(payload));
      setEditing(false);
      setSuccess("Gym profile saved.");
    } catch (failure) {
      setError(failureMessage(failure, "Unable to save the gym profile."));
    } finally {
      onPending(false);
    }
  }

  function replacePreview(file: File | null) {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    if (!file) {
      previewUrlRef.current = null;
      setPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(file);
    previewUrlRef.current = url;
    setPreviewUrl(url);
  }

  async function changeLogo(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null;
    event.target.value = "";
    if (!file) return;
    replacePreview(file);
    setSuccess(null);
    onPending(true);
    setError(null);
    try {
      setProfile(await uploadGymLogo(file));
      setSuccess("Gym logo saved.");
    } catch (failure) {
      replacePreview(null);
      setError(failureMessage(failure, "Unable to save the gym logo."));
    } finally {
      onPending(false);
    }
  }

  async function removeLogo() {
    setSuccess(null);
    onPending(true);
    setError(null);
    try {
      setProfile(await deleteGymLogo());
      replacePreview(null);
      setSuccess("Gym logo removed.");
    } catch (failure) {
      setError(failureMessage(failure, "Unable to remove the gym logo."));
    } finally {
      onPending(false);
    }
  }

  return (
    <section aria-labelledby="gym-profile-title" className={panelClass}>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-4 sm:px-5">
        <div>
          <h2
            className="text-lg font-bold text-slate-900"
            id="gym-profile-title"
          >
            {" "}
            {tx("Gym profile")}{" "}
          </h2>
          <p className="mt-0.5 text-sm text-slate-500">
            {" "}
            {tx("Name, contact, and address for this gym.")}{" "}
          </p>
        </div>
        {profile && canUpdate ? (
          <button
            className="button button-edit"
            onClick={() => setEditing(true)}
            type="button"
          >
            {" "}
            {tx("Edit gym profile")}{" "}
          </button>
        ) : null}
      </div>
      {loading ? (
        <div className="grid gap-3 p-5">
          <p className="text-sm font-medium text-slate-500" role="status">
            {" "}
            {tx("Loading gym profile.")}{" "}
          </p>
          {[0, 1, 2].map((item) => (
            <div
              className="h-16 animate-pulse rounded-xl bg-slate-100"
              key={item}
            />
          ))}
        </div>
      ) : (
        <div className="grid gap-5 p-4 sm:p-5">
          <Notice error={editing ? null : error} success={success} />
          {profile ? (
            <>
              <div className="grid items-start gap-8 lg:grid-cols-[20rem_minmax(0,1fr)]">
                <GymLogoPicker
                  canUpdate={canUpdate}
                  hasLogo={profileHasLogo(profile)}
                  pending={pending}
                  previewUrl={previewUrl}
                  onFile={(event) => void changeLogo(event)}
                  onRemove={() => void removeLogo()}
                />
                <div className="min-w-0 lg:pt-1">
                  <div className="flex flex-wrap items-center gap-3">
                    <p className="truncate text-2xl font-bold text-[#050505]">
                      {profile.name}
                    </p>
                    <Pill tone={recordTone(profile.status)}>
                      {tx(profile.status)}
                    </Pill>
                  </div>
                  <p className="mt-1 text-sm text-slate-500">{profile.code}</p>
                  <div className="mt-6">
                    <ProfileBlock
                      hint="How members and staff reach this gym."
                      rows={[
                        ...profileFields("contact").map((field) => ({
                          label: field.label,
                          value: textValue(profile[field.name]),
                        })),
                        { label: tx("Email"), value: textValue(profile.email) },
                      ]}
                      title={tx("Contact")}
                    />
                  </div>
                </div>
              </div>
              <div className="grid gap-8 lg:grid-cols-2">
                <ProfileBlock
                  rows={[
                    ...profileFields("address").map((field) => ({
                      label: field.label,
                      value: textValue(profile[field.name]),
                    })),
                    {
                      label: tx("Country code"),
                      value: textValue(profile.country_code),
                    },
                  ]}
                  title={tx("Address")}
                />
                <ProfileBlock
                  hint="Schedules, receipts, and reports use these."
                  rows={[
                    ...profileFields("locale").map((field) => ({
                      label: field.label,
                      value: textValue(profile[field.name]),
                    })),
                    {
                      label: tx("Currency"),
                      value: textValue(profile.currency),
                    },
                  ]}
                  title={tx("Time and currency")}
                />
              </div>
              <ProfileBlock
                hint="Lifecycle and subscription stay with platform administration."
                rows={[
                  {
                    label: tx("Subscription plan"),
                    value: textValue(profile.subscription_plan_name ?? ""),
                  },
                  {
                    label: tx("Subscription plan ID"),
                    value: textValue(profile.subscription_plan_id ?? ""),
                  },
                  {
                    label: tx("Subscription status"),
                    value: (
                      <Pill tone={recordTone(profile.subscription_status)}>
                        {tx(profile.subscription_status)}
                      </Pill>
                    ),
                  },
                  {
                    label: tx("Subscription starts"),
                    value: profileWhen(profile.subscription_starts_at),
                  },
                  {
                    label: tx("Subscription ends"),
                    value: profileWhen(profile.subscription_ends_at),
                  },
                  {
                    label: tx("Created"),
                    value: profileWhen(profile.created_at),
                  },
                  {
                    label: tx("Updated"),
                    value: profileWhen(profile.updated_at),
                  },
                ]}
                title={tx("Platform record")}
              />
            </>
          ) : null}
        </div>
      )}
      {profile && canUpdate && editing ? (
        <FormDialog
          description="Name, contact, address, and locale for this gym."
          key={JSON.stringify(profile)}
          onClose={() => {
            if (!pending) setEditing(false);
          }}
          onSubmit={save}
          pending={pending}
          title={tx("Gym profile")}
          wide
        >
          <FormSection title={tx("Identity")}>
            <ProfileControl name="name" profile={profile} />
          </FormSection>
          <FormSection
            hint="Leave email blank if this gym has none."
            title={tx("Contact")}
          >
            <ProfileControl name="phone" profile={profile} />
            <TextControl
              autoComplete="email"
              defaultValue={profile.email}
              label="Email"
              maxLength={254}
              name="email"
              type="email"
            />
          </FormSection>
          <FormSection title={tx("Address")}>
            {profileFields("address").map((field) => (
              <ProfileControl
                key={field.name}
                name={field.name}
                profile={profile}
                {...(field.name.startsWith("address_line")
                  ? { className: "sm:col-span-2" }
                  : {})}
              />
            ))}
            <TextControl
              autoComplete="country"
              defaultValue={profile.country_code}
              hint="Two letters, such as MM. Leave blank if unknown."
              label="Country code"
              maxLength={2}
              name="country_code"
              spellCheck={false}
              uppercase
            />
          </FormSection>
          <FormSection title={tx("Time and currency")}>
            <ProfileControl
              hint="Example: Asia/Yangon"
              name="timezone"
              profile={profile}
            />
            <div>
              <label className="field">
                <span>{tx("Currency")}</span>
                <select
                  aria-label={tx("Currency")}
                  className="input"
                  defaultValue={profile.currency === "MMK" ? "MMK" : "THB"}
                  name="currency"
                  required
                >
                  <option value="THB">{tx("THB")}</option>
                  <option value="MMK">{tx("MMK")}</option>
                </select>
              </label>
              <p className="mt-1 text-xs leading-5 text-slate-500">
                {" "}
                {tx("THB or MMK. It must match the subscription plan.")}{" "}
              </p>
            </div>
          </FormSection>
          {error ? (
            <p className="error" role="alert">
              {tx(error)}
            </p>
          ) : null}
          <div>
            <button
              className="button button-edit"
              disabled={pending}
              type="submit"
            >
              {" "}
              {tx("Save gym profile")}{" "}
            </button>
          </div>
        </FormDialog>
      ) : null}
    </section>
  );
}

function SettingsSection({
  canManage,
  pending,
  onPending,
}: {
  canManage: boolean;
  pending: boolean;
  onPending: (value: boolean) => void;
}) {
  const tx = useTx();
  const [settings, setSettings] = useState<GymSettings | null>(null);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    void getGymSettings()
      .then((value) => {
        if (active) setSettings(value);
      })
      .catch((failure: unknown) => {
        if (active)
          setError(
            failureMessage(failure, "Unable to load operational settings."),
          );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSuccess(null);
    let payload: GymSettingsUpdate;
    try {
      payload = readSettings(new FormData(event.currentTarget), tx);
    } catch (failure) {
      setError(
        failureMessage(
          failure,
          "Check the operational settings and try again.",
        ),
      );
      return;
    }
    onPending(true);
    setError(null);
    try {
      setSettings(await updateGymSettings(payload));
      setEditing(false);
      setSuccess("Operational settings saved.");
    } catch (failure) {
      setError(failureMessage(failure, "Unable to save operational settings."));
    } finally {
      onPending(false);
    }
  }

  const weekday =
    settings && settings.week_starts_on >= 0 && settings.week_starts_on <= 6
      ? WEEKDAYS[settings.week_starts_on]
      : "";

  return (
    <section
      aria-labelledby="operational-settings-title"
      className={panelClass}
    >
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-4 sm:px-5">
        <div>
          <h2
            className="text-lg font-bold text-slate-900"
            id="operational-settings-title"
          >
            {" "}
            {tx("Operational settings")}{" "}
          </h2>
          <p className="mt-0.5 text-sm text-slate-500">
            {" "}
            {tx("Check-in, numbers, classes, and reminders.")}{" "}
          </p>
        </div>
        {settings && canManage ? (
          <button
            className="button button-edit"
            onClick={() => setEditing(true)}
            type="button"
          >
            {" "}
            {tx("Edit operational settings")}{" "}
          </button>
        ) : null}
      </div>
      {loading ? (
        <div className="grid gap-3 p-5">
          <p className="text-sm font-medium text-slate-500" role="status">
            {" "}
            {tx("Loading operational settings.")}{" "}
          </p>
          {[0, 1, 2].map((item) => (
            <div
              className="h-16 animate-pulse rounded-xl bg-slate-100"
              key={item}
            />
          ))}
        </div>
      ) : (
        <div className="grid gap-5 p-4 sm:p-5">
          <Notice error={editing ? null : error} success={success} />
          {settings ? (
            <>
              <SettingGroup
                hint="Weekly schedules and reports start on this day."
                title={tx("Calendar")}
              >
                <Fact label="Week starts on">{textValue(weekday)}</Fact>
              </SettingGroup>
              <SettingGroup
                hint="New members receive the next number unless manual numbers are allowed."
                title={tx("Member numbers")}
              >
                <Fact label="Next member number">
                  {assignedMemberNumber(
                    settings.member_number_prefix,
                    settings.next_member_number,
                  )}
                </Fact>
                <Fact label="Allow manual member numbers">
                  <Choice on={settings.allow_manual_member_numbers} />
                </Fact>
              </SettingGroup>
              <SettingGroup
                hint="Example of the printed number. The sequence increases with each receipt."
                title={tx("Receipts")}
              >
                <Fact label="Receipt number">
                  {receiptExample(settings.receipt_number_prefix)}
                </Fact>
              </SettingGroup>
              <SettingGroup
                hint="How the front desk accepts a check-in code."
                title={tx("Check-in")}
              >
                <Fact label="Check-in duplicate window (seconds)">
                  {textValue(settings.check_in_duplicate_window_seconds)}
                </Fact>
                <Fact label="Check-in challenge lifetime (seconds)">
                  {textValue(settings.check_in_challenge_ttl_seconds)}
                </Fact>
                <Fact label="Check-in challenge maximum uses">
                  {textValue(settings.check_in_challenge_max_uses)}
                </Fact>
              </SettingGroup>
              <SettingGroup
                hint="Days before a membership ends. Example: 7 means one week before."
                title={tx("Membership reminders")}
              >
                <Fact label="Membership expiry warning days" wide>
                  {settings.membership_expiry_warning_days.length === 0 ? (
                    textValue("")
                  ) : (
                    <span className="flex flex-wrap gap-2">
                      {settings.membership_expiry_warning_days.map((day) => (
                        <Pill
                          key={day}
                          tone="bg-slate-100 text-slate-700 ring-slate-500/10"
                        >
                          {day}
                        </Pill>
                      ))}
                    </span>
                  )}
                </Fact>
                <Fact label="Email membership expiry warnings">
                  <Choice on={settings.membership_expiry_email_enabled} />
                </Fact>
                <Fact label="Push membership expiry warnings">
                  <Choice on={settings.membership_expiry_push_enabled} />
                </Fact>
              </SettingGroup>
              <SettingGroup title={tx("Classes")}>
                <Fact label="Class cancellation cutoff (minutes)">
                  {textValue(settings.class_cancellation_cutoff_minutes)}
                </Fact>
                <Fact label="A class no-show consumes a session">
                  <Choice on={settings.class_no_show_consumes_session} />
                </Fact>
              </SettingGroup>
              <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h3 className="text-sm font-bold text-amber-950">
                    {" "}
                    {tx("Finance approval")}{" "}
                  </h3>
                  <Pill tone="bg-amber-50 text-amber-800 ring-amber-600/15">
                    {" "}
                    {tx("Required")}{" "}
                  </Pill>
                </div>
                <p className="mt-2 text-sm leading-6 text-amber-950">
                  {approvalCopy()}
                </p>
              </div>
              <SettingGroup title={tx("Record")}>
                <Fact label="Created">{textValue(settings.created_at)}</Fact>
                <Fact label="Updated">{textValue(settings.updated_at)}</Fact>
              </SettingGroup>
            </>
          ) : null}
        </div>
      )}
      {settings && canManage && editing ? (
        <FormDialog
          description="Check-in, numbers, classes, and reminders for this gym."
          key={settings.updated_at}
          onClose={() => {
            if (!pending) setEditing(false);
          }}
          onSubmit={save}
          pending={pending}
          title={tx("Operational settings")}
          wide
        >
          <FormSection
            hint="Weekly schedules and reports start on this day."
            title={tx("Calendar")}
          >
            <div>
              <label className="field">
                <span className="text-sm font-semibold text-slate-700">
                  {" "}
                  {tx("Week starts on")}{" "}
                </span>
                <select
                  aria-label={tx("Week starts on")}
                  className="input"
                  defaultValue={String(settings.week_starts_on)}
                  name="week_starts_on"
                >
                  {WEEKDAYS.map((day, index) => (
                    <option key={day} value={index}>
                      {day}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </FormSection>
          <FormSection title={tx("Member numbers")}>
            <TextControl
              defaultValue={settings.member_number_prefix}
              hint={tx(
                "The next member number will look like {example}. Use 1–20 letters, digits, or hyphens.",
                {
                  example: assignedMemberNumber(
                    settings.member_number_prefix,
                    settings.next_member_number,
                  ),
                },
              )}
              label="Member number prefix"
              maxLength={20}
              name="member_number_prefix"
              required
              spellCheck={false}
              uppercase
            />
            <SettingToggle
              hint="Staff can type a member number instead of taking the next one."
              name="allow_manual_member_numbers"
              settings={settings}
            />
          </FormSection>
          <FormSection title={tx("Receipts")}>
            <TextControl
              defaultValue={settings.receipt_number_prefix}
              hint={tx(
                "A receipt will look like {example}. Use 1–20 letters, digits, or hyphens.",
                {
                  example: receiptExample(settings.receipt_number_prefix),
                },
              )}
              label="Receipt number prefix"
              maxLength={20}
              name="receipt_number_prefix"
              required
              spellCheck={false}
              uppercase
            />
          </FormSection>
          <FormSection
            hint="How the front desk accepts a check-in code."
            title={tx("Check-in")}
          >
            <IntegerControl
              name="check_in_duplicate_window_seconds"
              settings={settings}
            />
            <IntegerControl
              name="check_in_challenge_ttl_seconds"
              settings={settings}
            />
            <IntegerControl
              name="check_in_challenge_max_uses"
              settings={settings}
            />
          </FormSection>
          <FormSection
            hint="Comma-separated days before expiry, from 0 to 365."
            title={tx("Membership reminders")}
          >
            <TextControl
              className="sm:col-span-2"
              defaultValue={settings.membership_expiry_warning_days.join(", ")}
              hint="Example: 3, 7, 14"
              label="Membership expiry warning days"
              name="membership_expiry_warning_days"
              spellCheck={false}
            />
            <SettingToggle
              hint="Send an email on each warning day."
              name="membership_expiry_email_enabled"
              settings={settings}
            />
            <SettingToggle
              hint="Send a push notification on each warning day."
              name="membership_expiry_push_enabled"
              settings={settings}
            />
          </FormSection>
          <FormSection title={tx("Classes")}>
            <IntegerControl
              name="class_cancellation_cutoff_minutes"
              settings={settings}
            />
            <SettingToggle
              hint="A missed class still uses one booked session."
              name="class_no_show_consumes_session"
              settings={settings}
            />
          </FormSection>
          <fieldset className="rounded-xl border border-amber-200 bg-amber-50/70 p-4">
            <legend className="px-1 text-sm font-bold text-amber-950">
              {" "}
              {tx("Finance approval")}{" "}
            </legend>
            <label className="flex items-center justify-between gap-4 rounded-xl border border-amber-200 bg-white px-3 py-3">
              <span className="text-sm font-semibold text-slate-800">
                {" "}
                {tx("Require separate finance approval")}{" "}
              </span>
              <input
                aria-describedby="finance-approval-note"
                className="size-4 shrink-0 accent-indigo-600"
                defaultChecked
                disabled
                name="require_separate_finance_approval"
                type="checkbox"
              />
            </label>
            <p
              className="mt-2 text-xs leading-5 text-amber-950/80"
              id="finance-approval-note"
            >
              {approvalCopy()}
            </p>
          </fieldset>
          <ReadOnlyRows
            rows={[
              {
                label: tx("Next member number"),
                value: settings.next_member_number,
              },
              { label: tx("Created"), value: settings.created_at },
              { label: tx("Updated"), value: settings.updated_at },
            ]}
          />
          {error ? (
            <p className="error" role="alert">
              {tx(error)}
            </p>
          ) : null}
          <div>
            <button
              className="button button-edit"
              disabled={pending}
              type="submit"
            >
              {" "}
              {tx("Save operational settings")}{" "}
            </button>
          </div>
        </FormDialog>
      ) : null}
    </section>
  );
}

export function GymSettingsManager({
  canViewProfile,
  canUpdateProfile,
  canViewSettings,
  canManageSettings,
}: GymSettingsAccess) {
  const tx = useTx();
  const [pending, setPending] = useState(false);
  const [tab, setTab] = useState<"profile" | "operations">(
    canViewProfile ? "profile" : "operations",
  );
  const both = canViewProfile && canViewSettings;
  const tabs = [
    canViewProfile ? (["profile", "Profile"] as const) : null,
    canViewSettings ? (["operations", "Operations"] as const) : null,
  ].filter((item) => item !== null);

  return (
    <div className="grid gap-5">
      {both ? (
        <div
          aria-label={tx("Gym settings sections")}
          className="inline-flex w-fit rounded-xl bg-slate-200/70 p-1"
          role="tablist"
        >
          {tabs.map(([id, label]) => (
            <button
              aria-controls={`${id}-panel`}
              aria-selected={tab === id}
              className={tabClass(tab === id)}
              id={`${id}-tab`}
              key={id}
              onClick={() => setTab(id)}
              role="tab"
              type="button"
            >
              {tx(label)}
            </button>
          ))}
        </div>
      ) : null}
      {canViewProfile ? (
        <div
          aria-labelledby={both ? "profile-tab" : undefined}
          hidden={both && tab !== "profile"}
          id={both ? "profile-panel" : undefined}
          role={both ? "tabpanel" : undefined}
        >
          <ProfileSection
            canUpdate={canUpdateProfile}
            onPending={setPending}
            pending={pending}
          />
        </div>
      ) : null}
      {canViewSettings ? (
        <div
          aria-labelledby={both ? "operations-tab" : undefined}
          hidden={both && tab !== "operations"}
          id={both ? "operations-panel" : undefined}
          role={both ? "tabpanel" : undefined}
        >
          <SettingsSection
            canManage={canManageSettings}
            onPending={setPending}
            pending={pending}
          />
        </div>
      ) : null}
    </div>
  );
}
