import type { Metadata } from "next";

import { BrandLogo } from "@/components/brand-logo";
import { StaffInvitationForm } from "@/features/auth/staff-invitation-form";

export const metadata: Metadata = { title: "Accept staff invitation" };

export default async function AcceptStaffInvitationPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const { token = "" } = await searchParams;
  return (
    <main className="grid min-h-screen place-items-center p-6">
      <section
        className="card w-full max-w-md"
        aria-labelledby="staff-invitation-title"
      >
        <BrandLogo />
        <h1 id="staff-invitation-title" className="mt-6 text-2xl font-bold">
          Accept staff invitation
        </h1>
        <p className="muted mt-2">
          Choose a password to activate this gym staff login. You can confirm
          your name and phone if they need a correction.
        </p>
        {token ? (
          <StaffInvitationForm token={token} />
        ) : (
          <p className="error mt-5" role="alert">
            This invitation link is incomplete.
          </p>
        )}
      </section>
    </main>
  );
}
