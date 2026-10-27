"use client";

import { useTx, useLocale } from "@/i18n/use-tx";
import {
  isMemberGender,
  MEMBER_GENDERS,
  type MemberProfileInput,
} from "@/features/operations/member-profile";
import {
  citiesForRegion,
  MYANMAR_REGIONS,
  type MyanmarPlace,
} from "@/features/operations/myanmar-locations";
import type { Locale } from "@/i18n/locale";

const GENDER_LABELS: Record<(typeof MEMBER_GENDERS)[number], string> = {
  FEMALE: "Female",
  MALE: "Male",
  OTHER: "Other",
  UNSPECIFIED: "Unspecified",
};

function placeLabel(place: MyanmarPlace, locale: Locale): string {
  return locale === "mm" ? place.my : place.value;
}

function placesWithCurrent(
  places: readonly MyanmarPlace[],
  current: string,
): MyanmarPlace[] {
  if (!current || places.some((place) => place.value === current)) {
    return [...places];
  }
  return [{ value: current, my: current }, ...places];
}

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
  const locale = useLocale();
  function update<K extends keyof MemberProfileInput>(
    key: K,
    fieldValue: MemberProfileInput[K],
  ) {
    onChange({ ...value, [key]: fieldValue });
  }

  function updateRegion(region: string) {
    const stillValid = citiesForRegion(region).some(
      (place) => place.value === value.city,
    );
    onChange({
      ...value,
      region,
      city: stillValid ? value.city : "",
    });
  }

  const regions = placesWithCurrent(MYANMAR_REGIONS, value.region).sort(
    (a, b) =>
      placeLabel(a, locale).localeCompare(
        placeLabel(b, locale),
        locale === "mm" ? "my" : "en",
      ),
  );
  const cities = placesWithCurrent(
    citiesForRegion(value.region),
    value.city,
  ).sort((a, b) =>
    placeLabel(a, locale).localeCompare(
      placeLabel(b, locale),
      locale === "mm" ? "my" : "en",
    ),
  );

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
          <label className="field min-w-0 items-start">
            {tx("Region")}
            <select
              className="input w-0 max-w-full min-w-full"
              disabled={disabled}
              onChange={(event) => updateRegion(event.target.value)}
              value={value.region}
            >
              <option value="">{tx("Not specified")}</option>
              {regions.map((region) => (
                <option key={region.value} value={region.value}>
                  {placeLabel(region, locale)}
                </option>
              ))}
            </select>
          </label>
          <div className="grid min-w-0 content-start gap-1">
            <label className="field min-w-0 items-start">
              {tx("City")}
              <select
                aria-describedby={value.region ? undefined : "member-city-hint"}
                className="input w-0 max-w-full min-w-full"
                disabled={disabled || !value.region}
                onChange={(event) => update("city", event.target.value)}
                value={value.city}
              >
                <option value="">{tx("Not specified")}</option>
                {cities.map((cityOption) => (
                  <option key={cityOption.value} value={cityOption.value}>
                    {placeLabel(cityOption, locale)}
                  </option>
                ))}
              </select>
            </label>
            {value.region ? null : (
              <span className="muted" id="member-city-hint">
                {tx("Choose a region first.")}
              </span>
            )}
          </div>
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
