import {
  isMemberGender,
  MEMBER_GENDERS,
  type MemberProfileInput,
} from "@/features/operations/member-profile";

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
            Member number
            <input
              aria-busy={memberNumberPreview.status === "loading"}
              aria-describedby="member-number-hint"
              className="input bg-slate-50 text-slate-500"
              disabled
              placeholder={
                memberNumberPreview.status === "ready"
                  ? undefined
                  : memberNumberPreview.status === "loading"
                    ? "Assigning…"
                    : "Assigned on save"
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
              ? "Loading the next member number."
              : memberNumberPreview.status === "ready"
                ? "Assigned by the gym."
                : "The gym assigns the next number when you save."}
          </span>
        </div>
      ) : null}
      <label className="field">
        First name
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
        Last name
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
        Phone
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
        Email
        <input
          className="input"
          disabled={disabled}
          onChange={(event) => update("email", event.target.value)}
          type="email"
          value={value.email}
        />
      </label>
      <label className="field">
        Gender
        <select
          className="input"
          disabled={disabled}
          onChange={(event) => {
            const next = event.target.value;
            update("gender", isMemberGender(next) ? next : "");
          }}
          value={value.gender}
        >
          <option value="">Not specified</option>
          {MEMBER_GENDERS.map((gender) => (
            <option key={gender} value={gender}>
              {GENDER_LABELS[gender]}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        Date of birth
        <input
          className="input"
          disabled={disabled}
          max={value.join_date || undefined}
          onChange={(event) => update("date_of_birth", event.target.value)}
          type="date"
          value={value.date_of_birth}
        />
      </label>
      <fieldset className="grid gap-4 sm:grid-cols-2" disabled={disabled}>
        <legend className="mb-3 font-semibold">Address</legend>
        <label className="field">
          Address line 1
          <input
            className="input"
            maxLength={255}
            onChange={(event) => update("address_line1", event.target.value)}
            value={value.address_line1}
          />
        </label>
        <label className="field">
          Address line 2
          <input
            className="input"
            maxLength={255}
            onChange={(event) => update("address_line2", event.target.value)}
            value={value.address_line2}
          />
        </label>
        <label className="field">
          City
          <input
            className="input"
            maxLength={100}
            onChange={(event) => update("city", event.target.value)}
            value={value.city}
          />
        </label>
        <label className="field">
          Region
          <input
            className="input"
            maxLength={100}
            onChange={(event) => update("region", event.target.value)}
            value={value.region}
          />
        </label>
        <label className="field">
          Postal code
          <input
            className="input"
            maxLength={20}
            onChange={(event) => update("postal_code", event.target.value)}
            value={value.postal_code}
          />
        </label>
        <label className="field">
          Country code
          <input
            className="input"
            maxLength={2}
            onChange={(event) => update("country_code", event.target.value)}
            value={value.country_code}
          />
        </label>
      </fieldset>
      <label className="field">
        Join date
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
        Emergency contact name
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
        Emergency contact phone
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
        Notes
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
