import { pageMetadata } from "@/i18n/metadata";
import { Tx } from "@/i18n/tx";

import { BrandLogo } from "@/components/brand-logo";
import { StaffInvitationForm } from "@/features/auth/staff-invitation-form";

export async function generateMetadata() {
  return pageMetadata("Accept staff invitation");
}

export default async function AcceptStaffInvitationPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const { token = "" } = await searchParams;
  return (
    <main className="grid w-full flex-1 place-items-center px-6 py-10">
      <section
        className="card w-full max-w-md"
        aria-labelledby="staff-invitation-title"
      >
        <BrandLogo />
        <h1 id="staff-invitation-title" className="mt-6 text-2xl font-bold">
          {" "}
          <Tx text="Accept staff invitation" />{" "}
        </h1>
        <p className="muted mt-2">
          {" "}
          <Tx text="Choose a password to activate this gym staff login. You can confirm your name and phone if they need a correction." />{" "}
        </p>
        {token ? (
          <StaffInvitationForm token={token} />
        ) : (
          <p className="error mt-5" role="alert">
            {" "}
            <Tx text="This invitation link is incomplete." />{" "}
          </p>
        )}
      </section>
    </main>
  );
}
