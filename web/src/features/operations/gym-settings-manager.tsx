"use client";

import { useEffect, useState, type FormEvent, type ReactNode } from "react";

import {
  getGymProfile,
  getGymSettings,
  updateGymProfile,
  updateGymSettings,
} from "@/features/operations/client";
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
  legal_name: 200,
  phone: 32,
  address_line1: 255,
  address_line2: 255,
  city: 100,
  region: 100,
  postal_code: 20,
  timezone: 64,
} as const;

type ProfileTextField = keyof typeof PROFILE_LIMITS;

const PROFILE_FIELDS: { name: ProfileTextField; label: string }[] = [
  { name: "name", label: "Name" },
  { name: "legal_name", label: "Legal name" },
  { name: "phone", label: "Phone" },
  { name: "address_line1", label: "Address line 1" },
  { name: "address_line2", label: "Address line 2" },
  { name: "city", label: "City" },
  { name: "region", label: "Region" },
  { name: "postal_code", label: "Postal code" },
  { name: "timezone", label: "Timezone" },
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

function limitedText(
  form: FormData,
  field: ProfileTextField,
  label: string,
  required = false,
): string {
  const value = String(form.get(field) ?? "").trim();
  const max = PROFILE_LIMITS[field];
  if (required && value.length === 0) {
    throw new Error(`Enter a ${label.toLowerCase()}.`);
  }
  if (value.length > max) {
    throw new Error(`Enter a ${label.toLowerCase()} up to ${max} characters.`);
  }
  return value;
}

function readProfile(form: FormData): GymProfileUpdate {
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
  if (!/^[A-Z]{3}$/.test(currency)) {
    throw new Error("Enter a three-letter currency code.");
  }
  return {
    name: limitedText(form, "name", "Name", true),
    legal_name: limitedText(form, "legal_name", "Legal name"),
    email,
    phone: limitedText(form, "phone", "Phone"),
    address_line1: limitedText(form, "address_line1", "Address line 1"),
    address_line2: limitedText(form, "address_line2", "Address line 2"),
    city: limitedText(form, "city", "City"),
    region: limitedText(form, "region", "Region"),
    postal_code: limitedText(form, "postal_code", "Postal code"),
    country_code: countryCode,
    timezone: limitedText(form, "timezone", "Timezone", true),
    currency,
  };
}

function readInteger(
  form: FormData,
  name: string,
  label: string,
  min: number,
  max: number,
): number {
  const text = String(form.get(name) ?? "").trim();
  if (!/^\d+$/.test(text)) {
    throw new Error(`Enter a whole number for ${label}.`);
  }
  const value = Number(text);
  if (value < min || value > max) {
    throw new Error(`Enter a value from ${min} to ${max} for ${label}.`);
  }
  return value;
}

function readPrefix(form: FormData, name: string, label: string): string {
  const prefix = String(form.get(name) ?? "")
    .trim()
    .toUpperCase();
  if (!/^[A-Z0-9-]{1,20}$/.test(prefix)) {
    throw new Error(
      `${label} must be 1 to 20 uppercase letters, digits, or hyphens.`,
    );
  }
  return prefix;
}

function readWarningDays(form: FormData): number[] {
  const text = String(form.get("membership_expiry_warning_days") ?? "").trim();
  if (!text) return [];
  const parts = text
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
  if (parts.length > 366) {
    throw new Error("Enter at most 366 warning days.");
  }
  const days = parts.map((part) => {
    if (!/^\d+$/.test(part) || Number(part) > 365) {
      throw new Error("Each warning day must be a whole number from 0 to 365.");
    }
    return Number(part);
  });
  if (new Set(days).size !== days.length) {
    throw new Error("Warning days must be unique.");
  }
  return [...days].sort((left, right) => left - right);
}

function readSettings(form: FormData): GymSettingsUpdate {
  return {
    week_starts_on: readInteger(form, "week_starts_on", "Week starts on", 0, 6),
    allow_manual_member_numbers:
      form.get("allow_manual_member_numbers") === "on",
    member_number_prefix: readPrefix(
      form,
      "member_number_prefix",
      "Member number prefix",
    ),
    receipt_number_prefix: readPrefix(
      form,
      "receipt_number_prefix",
      "Receipt number prefix",
    ),
    check_in_duplicate_window_seconds: readInteger(
      form,
      "check_in_duplicate_window_seconds",
      "Check-in duplicate window (seconds)",
      0,
      86_400,
    ),
    check_in_challenge_ttl_seconds: readInteger(
      form,
      "check_in_challenge_ttl_seconds",
      "Check-in challenge lifetime (seconds)",
      30,
      600,
    ),
    check_in_challenge_max_uses: readInteger(
      form,
      "check_in_challenge_max_uses",
      "Check-in challenge maximum uses",
      1,
      100,
    ),
    membership_expiry_warning_days: readWarningDays(form),
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
    ),
    class_no_show_consumes_session:
      form.get("class_no_show_consumes_session") === "on",
    require_separate_finance_approval: true,
  };
}

function Notice({
  error,
  success,
}: {
  error: string | null;
  success: string | null;
}) {
  return (
    <>
      {error && (
        <p className="error mt-4" role="alert">
          {error}
        </p>
      )}
      {success && (
        <p className="mt-4 text-sm text-emerald-700" role="status">
          {success}
        </p>
      )}
    </>
  );
}

function ReadOnlyRows({
  rows,
}: {
  rows: { label: string; value: ReactNode }[];
}) {
  return (
    <dl className="mt-4 grid gap-3">
      {rows.map((row) => (
        <div key={row.label}>
          <dt className="text-sm font-semibold">{row.label}</dt>
          <dd>
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

function profileMetadata(profile: GymProfile) {
  return [
    { label: "Code", value: profile.code },
    { label: "Status", value: profile.status },
    { label: "Subscription plan", value: profile.subscription_plan_name ?? "" },
    {
      label: "Subscription plan ID",
      value: profile.subscription_plan_id ?? "",
    },
    { label: "Subscription status", value: profile.subscription_status },
    {
      label: "Subscription starts",
      value: profile.subscription_starts_at ?? "",
    },
    { label: "Subscription ends", value: profile.subscription_ends_at ?? "" },
    { label: "Created", value: profile.created_at },
    { label: "Updated", value: profile.updated_at },
  ];
}

function ProfileMetadata({ profile }: { profile: GymProfile }) {
  return (
    <div>
      <h3 className="mt-4 text-sm font-semibold">Platform-managed record</h3>
      <p className="muted text-sm">
        Code, lifecycle, and subscription are read-only. Platform administration
        changes them.
      </p>
      <ReadOnlyRows rows={profileMetadata(profile)} />
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
  const [profile, setProfile] = useState<GymProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

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
    let payload: GymProfileUpdate;
    try {
      payload = readProfile(new FormData(event.currentTarget));
    } catch (failure) {
      setError(failureMessage(failure, "Check the gym profile and try again."));
      return;
    }
    onPending(true);
    setError(null);
    try {
      setProfile(await updateGymProfile(payload));
      setSuccess("Gym profile saved.");
    } catch (failure) {
      setError(failureMessage(failure, "Unable to save the gym profile."));
    } finally {
      onPending(false);
    }
  }

  return (
    <section
      aria-labelledby="gym-profile-title"
      className="card w-auto max-w-none"
    >
      <h2 className="text-xl font-bold" id="gym-profile-title">
        Gym profile
      </h2>
      {loading && (
        <p className="muted mt-4" role="status">
          Loading gym profile.
        </p>
      )}
      <Notice error={error} success={success} />
      {profile && <ProfileMetadata profile={profile} />}
      {profile && !canUpdate && (
        <ReadOnlyRows
          rows={[
            ...PROFILE_FIELDS.map((field) => ({
              label: field.label,
              value: profile[field.name],
            })),
            { label: "Email", value: profile.email },
            { label: "Country code", value: profile.country_code },
            { label: "Currency", value: profile.currency },
          ]}
        />
      )}
      {profile && canUpdate && (
        <form
          className="mt-4 grid gap-3"
          key={JSON.stringify(profile)}
          onSubmit={save}
        >
          {PROFILE_FIELDS.map((field) => (
            <label className="field" key={field.name}>
              <span>{field.label}</span>
              <input
                className="input"
                defaultValue={profile[field.name]}
                maxLength={PROFILE_LIMITS[field.name]}
                name={field.name}
                required={field.name === "name" || field.name === "timezone"}
              />
            </label>
          ))}
          <label className="field">
            <span>Email</span>
            <input
              className="input"
              defaultValue={profile.email}
              maxLength={254}
              name="email"
              type="email"
            />
          </label>
          <label className="field">
            <span>Country code</span>
            <input
              className="input"
              defaultValue={profile.country_code}
              maxLength={2}
              name="country_code"
            />
          </label>
          <label className="field">
            <span>Currency</span>
            <input
              className="input"
              defaultValue={profile.currency}
              maxLength={3}
              name="currency"
              required
            />
          </label>
          <button
            className="button button-edit"
            disabled={pending}
            type="submit"
          >
            Save gym profile
          </button>
        </form>
      )}
    </section>
  );
}

function approvalCopy() {
  return "Separate finance approval is mandatory and cannot be turned off. Payments, expenses, retail sales, PT sales, and salary records must be approved by a different authorized manager or owner.";
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
  const [settings, setSettings] = useState<GymSettings | null>(null);
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
      payload = readSettings(new FormData(event.currentTarget));
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
      className="card w-auto max-w-none"
    >
      <h2 className="text-xl font-bold" id="operational-settings-title">
        Operational settings
      </h2>
      {loading && (
        <p className="muted mt-4" role="status">
          Loading operational settings.
        </p>
      )}
      <Notice error={error} success={success} />
      {settings && !canManage && (
        <>
          <ReadOnlyRows
            rows={[
              { label: "Week starts on", value: weekday },
              {
                label: "Allow manual member numbers",
                value: settings.allow_manual_member_numbers ? "Yes" : "No",
              },
              {
                label: "Member number prefix",
                value: settings.member_number_prefix,
              },
              {
                label: "Receipt number prefix",
                value: settings.receipt_number_prefix,
              },
              {
                label: "Check-in duplicate window (seconds)",
                value: settings.check_in_duplicate_window_seconds,
              },
              {
                label: "Check-in challenge lifetime (seconds)",
                value: settings.check_in_challenge_ttl_seconds,
              },
              {
                label: "Check-in challenge maximum uses",
                value: settings.check_in_challenge_max_uses,
              },
              {
                label: "Membership expiry warning days",
                value: settings.membership_expiry_warning_days.join(", "),
              },
              {
                label: "Email membership expiry warnings",
                value: settings.membership_expiry_email_enabled ? "Yes" : "No",
              },
              {
                label: "Push membership expiry warnings",
                value: settings.membership_expiry_push_enabled ? "Yes" : "No",
              },
              {
                label: "Class cancellation cutoff (minutes)",
                value: settings.class_cancellation_cutoff_minutes,
              },
              {
                label: "A class no-show consumes a session",
                value: settings.class_no_show_consumes_session ? "Yes" : "No",
              },
              { label: "Separate finance approval", value: "Required" },
              {
                label: "Next member number",
                value: settings.next_member_number,
              },
              { label: "Created", value: settings.created_at },
              { label: "Updated", value: settings.updated_at },
            ]}
          />
          <p className="muted mt-4 text-sm">{approvalCopy()}</p>
        </>
      )}
      {settings && canManage && (
        <form
          className="mt-4 grid gap-3"
          key={settings.updated_at}
          onSubmit={save}
        >
          <label className="field">
            <span>Week starts on</span>
            <select
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
          {BOOLEAN_FIELDS.map((field) => (
            <label className="flex items-start gap-3" key={field.name}>
              <input
                className="mt-1"
                defaultChecked={settings[field.name]}
                name={field.name}
                type="checkbox"
              />
              <span>{field.label}</span>
            </label>
          ))}
          <label className="field">
            <span>Member number prefix</span>
            <input
              className="input"
              defaultValue={settings.member_number_prefix}
              maxLength={20}
              name="member_number_prefix"
              required
            />
          </label>
          <label className="field">
            <span>Receipt number prefix</span>
            <input
              className="input"
              defaultValue={settings.receipt_number_prefix}
              maxLength={20}
              name="receipt_number_prefix"
              required
            />
          </label>
          {INTEGER_FIELDS.map((field) => (
            <label className="field" key={field.name}>
              <span>{field.label}</span>
              <input
                className="input"
                defaultValue={settings[field.name]}
                max={field.max}
                min={field.min}
                name={field.name}
                required
                step={1}
                type="number"
              />
            </label>
          ))}
          <label className="field">
            <span>Membership expiry warning days</span>
            <input
              className="input"
              defaultValue={settings.membership_expiry_warning_days.join(", ")}
              name="membership_expiry_warning_days"
            />
          </label>
          <fieldset className="rounded-lg border border-slate-200 p-3">
            <legend className="px-1 text-sm font-semibold">
              Finance approval
            </legend>
            <label className="mt-2 flex items-start gap-3">
              <input
                aria-describedby="finance-approval-note"
                className="mt-1"
                defaultChecked
                disabled
                name="require_separate_finance_approval"
                type="checkbox"
              />
              <span>Require separate finance approval</span>
            </label>
            <p className="muted mt-2 text-sm" id="finance-approval-note">
              {approvalCopy()}
            </p>
          </fieldset>
          <ReadOnlyRows
            rows={[
              {
                label: "Next member number",
                value: settings.next_member_number,
              },
              { label: "Created", value: settings.created_at },
              { label: "Updated", value: settings.updated_at },
            ]}
          />
          <button
            className="button button-edit"
            disabled={pending}
            type="submit"
          >
            Save operational settings
          </button>
        </form>
      )}
    </section>
  );
}

export function GymSettingsManager({
  canViewProfile,
  canUpdateProfile,
  canViewSettings,
  canManageSettings,
}: GymSettingsAccess) {
  const [pending, setPending] = useState(false);

  return (
    <div className="grid items-start gap-6 xl:grid-cols-2">
      {canViewProfile && (
        <ProfileSection
          canUpdate={canUpdateProfile}
          onPending={setPending}
          pending={pending}
        />
      )}
      {canViewSettings && (
        <SettingsSection
          canManage={canManageSettings}
          onPending={setPending}
          pending={pending}
        />
      )}
    </div>
  );
}
