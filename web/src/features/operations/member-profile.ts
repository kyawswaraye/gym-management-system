import type {
  Gender,
  Member,
  MemberAddressRequest,
  MemberCreateRequest,
  MemberUpdateRequest,
} from "@/types/api";

export const MEMBER_GENDERS = [
  "FEMALE",
  "MALE",
  "OTHER",
  "UNSPECIFIED",
] as const satisfies readonly Gender[];

export type MemberGenderInput = "" | Gender;

export type MemberProfileInput = {
  member_number: string;
  first_name: string;
  last_name: string;
  phone: string;
  email: string;
  gender: MemberGenderInput;
  date_of_birth: string;
  address_line1: string;
  address_line2: string;
  city: string;
  region: string;
  postal_code: string;
  country_code: string;
  join_date: string;
  emergency_contact_name: string;
  emergency_contact_phone: string;
  notes: string;
};

export type MemberCreatePayload = Omit<
  MemberCreateRequest,
  "profile_photo_asset_id"
>;

export type MemberUpdatePayload = Omit<
  MemberUpdateRequest,
  "profile_photo_asset_id"
>;

export function isMemberGender(value: string): value is Gender {
  return (MEMBER_GENDERS as readonly string[]).includes(value);
}

function collapse(value: string): string {
  return value.trim().split(/\s+/).filter(Boolean).join(" ");
}

function trimmed(value: string): string {
  return value.trim();
}

function blankToNull(value: string): string | null {
  const text = trimmed(value);
  return text ? text : null;
}

function addressPayload(input: MemberProfileInput): MemberAddressRequest {
  return {
    address_line1: collapse(input.address_line1),
    address_line2: collapse(input.address_line2),
    city: collapse(input.city),
    region: collapse(input.region),
    postal_code: collapse(input.postal_code),
    country_code: trimmed(input.country_code).toUpperCase(),
  };
}

type MemberProfilePayload = {
  first_name: string;
  last_name: string;
  phone: string | null;
  email: string | null;
  gender: Gender | null;
  date_of_birth: string | null;
  address: MemberAddressRequest;
  join_date: string;
  emergency_contact_name: string;
  emergency_contact_phone: string;
  notes: string;
};

function profilePayload(input: MemberProfileInput): MemberProfilePayload {
  const email = blankToNull(input.email);
  return {
    first_name: collapse(input.first_name),
    last_name: collapse(input.last_name),
    phone: blankToNull(input.phone),
    email: email ? email.toLowerCase() : null,
    gender: input.gender || null,
    date_of_birth: input.date_of_birth || null,
    address: addressPayload(input),
    join_date: input.join_date,
    emergency_contact_name: collapse(input.emergency_contact_name),
    emergency_contact_phone: trimmed(input.emergency_contact_phone),
    notes: trimmed(input.notes),
  };
}

export function emptyMemberProfile(joinDate: string): MemberProfileInput {
  return {
    member_number: "",
    first_name: "",
    last_name: "",
    phone: "",
    email: "",
    gender: "",
    date_of_birth: "",
    address_line1: "",
    address_line2: "",
    city: "",
    region: "",
    postal_code: "",
    country_code: "",
    join_date: joinDate,
    emergency_contact_name: "",
    emergency_contact_phone: "",
    notes: "",
  };
}

export function memberProfileFromMember(member: Member): MemberProfileInput {
  return {
    member_number: member.member_number,
    first_name: member.first_name,
    last_name: member.last_name,
    phone: member.phone ?? "",
    email: member.email ?? "",
    gender: member.gender ?? "",
    date_of_birth: member.date_of_birth ?? "",
    address_line1: member.address.address_line1,
    address_line2: member.address.address_line2,
    city: member.address.city,
    region: member.address.region,
    postal_code: member.address.postal_code,
    country_code: member.address.country_code,
    join_date: member.join_date,
    emergency_contact_name: member.emergency_contact_name,
    emergency_contact_phone: member.emergency_contact_phone,
    notes: member.notes,
  };
}

export function memberProfileSnapshot(member: Member): string {
  return JSON.stringify(memberProfileFromMember(member));
}

export function memberCreatePayload(
  input: MemberProfileInput,
): MemberCreatePayload {
  return {
    member_number: trimmed(input.member_number),
    ...profilePayload(input),
  };
}

export function memberUpdatePayload(
  input: MemberProfileInput,
): MemberUpdatePayload {
  return profilePayload(input);
}
