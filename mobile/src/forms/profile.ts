import { z } from "zod";

import type { Schemas } from "../api/memberApi";

export const profileFormSchema = z
  .object({
    phone: z.string(),
    email: z.string(),
    gender: z.enum(["", "FEMALE", "MALE", "OTHER", "UNSPECIFIED"]),
    date_of_birth: z.string(),
    address_line1: z.string(),
    address_line2: z.string(),
    city: z.string(),
    region: z.string(),
    postal_code: z.string(),
    country_code: z.string(),
    emergency_contact_name: z.string(),
    emergency_contact_phone: z.string(),
  })
  .superRefine((value, context) => {
    if (value.email && !z.email().safeParse(value.email).success) {
      context.addIssue({
        code: "custom",
        path: ["email"],
        message: "Enter a valid email address.",
      });
    }
    if (
      value.date_of_birth &&
      !/^\d{4}-\d{2}-\d{2}$/.test(value.date_of_birth)
    ) {
      context.addIssue({
        code: "custom",
        path: ["date_of_birth"],
        message: "Use a YYYY-MM-DD date.",
      });
    }
    if (value.country_code && !/^[A-Za-z]{2}$/.test(value.country_code)) {
      context.addIssue({
        code: "custom",
        path: ["country_code"],
        message: "Use a two-letter country code.",
      });
    }
  });

export type ProfileFormValues = z.infer<typeof profileFormSchema>;

export function profileToForm(
  profile: Schemas["MemberSelfProfile"],
): ProfileFormValues {
  return {
    phone: profile.phone ?? "",
    email: profile.email ?? "",
    gender: profile.gender ?? "",
    date_of_birth: profile.date_of_birth ?? "",
    address_line1: profile.address.address_line1 ?? "",
    address_line2: profile.address.address_line2 ?? "",
    city: profile.address.city ?? "",
    region: profile.address.region ?? "",
    postal_code: profile.address.postal_code ?? "",
    country_code: profile.address.country_code ?? "",
    emergency_contact_name: profile.emergency_contact_name,
    emergency_contact_phone: profile.emergency_contact_phone,
  };
}

export function profilePatch(
  current: ProfileFormValues,
  next: ProfileFormValues,
): Schemas["PatchedMemberSelfUpdateRequest"] | null {
  const patch: Schemas["PatchedMemberSelfUpdateRequest"] = {};
  if (next.phone !== current.phone) {
    patch.phone = next.phone ? next.phone : null;
  }
  if (next.email !== current.email) {
    patch.email = next.email ? next.email : null;
  }
  if (next.gender !== current.gender) {
    patch.gender = next.gender ? next.gender : null;
  }
  if (next.date_of_birth !== current.date_of_birth) {
    patch.date_of_birth = next.date_of_birth ? next.date_of_birth : null;
  }
  if (next.emergency_contact_name !== current.emergency_contact_name) {
    patch.emergency_contact_name = next.emergency_contact_name;
  }
  if (next.emergency_contact_phone !== current.emergency_contact_phone) {
    patch.emergency_contact_phone = next.emergency_contact_phone;
  }
  const addressChanged =
    next.address_line1 !== current.address_line1 ||
    next.address_line2 !== current.address_line2 ||
    next.city !== current.city ||
    next.region !== current.region ||
    next.postal_code !== current.postal_code ||
    next.country_code !== current.country_code;
  if (addressChanged) {
    const empty =
      !next.address_line1 &&
      !next.address_line2 &&
      !next.city &&
      !next.region &&
      !next.postal_code &&
      !next.country_code;
    patch.address = empty
      ? null
      : {
          address_line1: next.address_line1,
          address_line2: next.address_line2,
          city: next.city,
          region: next.region,
          postal_code: next.postal_code,
          country_code: next.country_code.toUpperCase(),
        };
  }
  return Object.keys(patch).length ? patch : null;
}
