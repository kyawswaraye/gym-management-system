"use client";

import { useTx, useLocale } from "@/i18n/use-tx";
import type { Locale } from "@/i18n/locale";
import {
  isMemberGender,
  MEMBER_GENDERS,
  type MemberProfileInput,
} from "@/features/operations/member-profile";
import { formatAppDate } from "@/lib/date";
import {
  myanmarPlaceText,
  MyanmarRegionCityFields,
} from "@/features/operations/myanmar-place-fields";

const GENDER_LABELS: Record<(typeof MEMBER_GENDERS)[number], string> = {
  FEMALE: "Female",
  MALE: "Male",
  OTHER: "Other",
  UNSPECIFIED: "Unspecified",
};

export type MemberNumberPreview = {
  status: "loading" | "ready" | "unavailable";
  value: string;
};

export function MemberProfileFields({
  value,
  onChange,
  disabled,
  includeMemberNumber,
  memberNumberPreview = { status: "unavailable", value: "" },
}: {
  value: MemberProfileInput;
  onChange: (next: MemberProfileInput) => void;
  disabled: boolean;
  includeMemberNumber: boolean;
  memberNumberPreview?: MemberNumberPreview;
}) {
  const tx = useTx();
  function update<K extends keyof MemberProfileInput>(
    key: K,
    fieldValue: MemberProfileInput[K],
  ) {
    onChange({ ...value, [key]: fieldValue });
  }

  return (
    <>
      {includeMemberNumber ? (
        <div className="grid gap-1 sm:col-span-2">
          <label className="field">
            {tx("Member number")}
            <input
              aria-busy={memberNumberPreview.status === "loading"}
              aria-describedby="member-number-hint"
              className="input bg-slate-50 text-slate-500"
              disabled
              placeholder={
                memberNumberPreview.status === "ready"
                  ? undefined
                  : memberNumberPreview.status === "loading"
                    ? tx("Assigning…")
                    : tx("Assigned on save")
              }
              readOnly
              value={
                memberNumberPreview.status === "ready"
                  ? memberNumberPreview.value
                  : ""
              }
            />
          </label>
          <span className="muted" id="member-number-hint">
            {memberNumberPreview.status === "loading"
              ? tx("Loading the next member number.")
              : memberNumberPreview.status === "ready"
                ? tx("Assigned by the gym.")
                : tx("The gym assigns the next number when you save.")}
          </span>
        </div>
      ) : null}
      <label className="field">
        {tx("First name")}
        <input
          className="input"
          disabled={disabled}
          maxLength={100}
          onChange={(event) => update("first_name", event.target.value)}
          required
          value={value.first_name}
        />
      </label>
      <label className="field">
        {tx("Last name")}
        <input
          className="input"
          disabled={disabled}
          maxLength={100}
          onChange={(event) => update("last_name", event.target.value)}
          required
          value={value.last_name}
        />
      </label>
      <label className="field">
        {tx("Phone")}
        <input
          className="input"
          disabled={disabled}
          maxLength={32}
          onChange={(event) => update("phone", event.target.value)}
          type="tel"
          value={value.phone}
        />
      </label>
      <label className="field">
        {tx("Email")}
        <input
          className="input"
          disabled={disabled}
          onChange={(event) => update("email", event.target.value)}
          type="email"
          value={value.email}
        />
      </label>
      <label className="field">
        {tx("Gender")}
        <select
          className="input"
          disabled={disabled}
          onChange={(event) => {
            const next = event.target.value;
            update("gender", isMemberGender(next) ? next : "");
          }}
          value={value.gender}
        >
          <option value="">{tx("Not specified")}</option>
          {MEMBER_GENDERS.map((gender) => (
            <option key={gender} value={gender}>
              {tx(GENDER_LABELS[gender])}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        {tx("Date of birth")}
        <input
          className="input"
          disabled={disabled}
          max={value.join_date || undefined}
          onChange={(event) => update("date_of_birth", event.target.value)}
          type="date"
          value={value.date_of_birth}
        />
      </label>
      <fieldset
        className="m-0 min-w-0 border-0 p-0 [min-inline-size:0]"
        disabled={disabled}
      >
        <legend className="mb-3 font-semibold">{tx("Address")}</legend>
        <div className="grid min-w-0 items-start gap-4 sm:grid-cols-2">
          <label className="field min-w-0">
            {tx("Address line 1")}
            <input
              className="input"
              maxLength={255}
              onChange={(event) => update("address_line1", event.target.value)}
              value={value.address_line1}
            />
          </label>
          <label className="field min-w-0">
            {tx("Address line 2")}
            <input
              className="input"
              maxLength={255}
              onChange={(event) => update("address_line2", event.target.value)}
              value={value.address_line2}
            />
          </label>
          <MyanmarRegionCityFields
            city={value.city}
            disabled={disabled}
            named={false}
            onChange={(next) =>
              onChange({ ...value, region: next.region, city: next.city })
            }
            region={value.region}
          />
          <label className="field min-w-0">
            {tx("Postal code")}
            <input
              className="input"
              maxLength={20}
              onChange={(event) => update("postal_code", event.target.value)}
              value={value.postal_code}
            />
          </label>
          <label className="field min-w-0">
            {tx("Country code")}
            <input
              className="input"
              maxLength={2}
              onChange={(event) => update("country_code", event.target.value)}
              value={value.country_code}
            />
          </label>
        </div>
      </fieldset>
      <label className="field">
        {tx("Join date")}
        <input
          className="input"
          disabled={disabled}
          onChange={(event) => update("join_date", event.target.value)}
          required
          type="date"
          value={value.join_date}
        />
      </label>
      <label className="field">
        {tx("Emergency contact name")}
        <input
          className="input"
          disabled={disabled}
          maxLength={150}
          onChange={(event) =>
            update("emergency_contact_name", event.target.value)
          }
          value={value.emergency_contact_name}
        />
      </label>
      <label className="field">
        {tx("Emergency contact phone")}
        <input
          className="input"
          disabled={disabled}
          maxLength={32}
          onChange={(event) =>
            update("emergency_contact_phone", event.target.value)
          }
          type="tel"
          value={value.emergency_contact_phone}
        />
      </label>
      <label className="field">
        {tx("Notes")}
        <textarea
          className="input"
          disabled={disabled}
          maxLength={4000}
          onChange={(event) => update("notes", event.target.value)}
          value={value.notes}
        />
      </label>
    </>
  );
}

function shown(value: string): string {
  const text = value.trim();
  return text || "—";
}

function namedPlace(value: string, locale: Locale): string {
  return myanmarPlaceText(value, locale) || "—";
}

export function MemberProfileDetails({ value }: { value: MemberProfileInput }) {
  const tx = useTx();
  const locale = useLocale();
  const gender = value.gender ? tx(GENDER_LABELS[value.gender]) : "";
  const rows: Array<[string, string, boolean]> = [
    ["Phone", shown(value.phone), false],
    ["Gender", shown(gender), false],
    ["Date of birth", shown(formatAppDate(value.date_of_birth)), false],
    ["Address line 1", shown(value.address_line1), false],
    ["Address line 2", shown(value.address_line2), false],
    ["Region", namedPlace(value.region, locale), false],
    ["City", namedPlace(value.city, locale), false],
    ["Postal code", shown(value.postal_code), false],
    ["Country code", shown(value.country_code), false],
    ["Join date", shown(formatAppDate(value.join_date)), false],
    ["Emergency contact name", shown(value.emergency_contact_name), false],
    ["Emergency contact phone", shown(value.emergency_contact_phone), false],
    ["Notes", shown(value.notes), true],
  ];
  return (
    <dl className="mt-4 grid gap-3 sm:grid-cols-2">
      {rows.map(([label, text, wide]) => (
        <div className={wide ? "sm:col-span-2" : undefined} key={label}>
          <dt className="text-xs font-semibold text-slate-500">{tx(label)}</dt>
          <dd className="mt-1 text-sm font-medium break-words whitespace-pre-wrap text-slate-900">
            {text}
          </dd>
        </div>
      ))}
    </dl>
  );
}
