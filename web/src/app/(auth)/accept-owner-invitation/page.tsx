import { Tx } from "@/i18n/tx";
import { BrandLogo } from "@/components/brand-logo";
import { OwnerInvitationForm } from "@/features/auth/owner-invitation-form";

export default async function AcceptOwnerInvitationPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const { token = "" } = await searchParams;
  return (
    <main className="grid w-full flex-1 place-items-center px-6 py-10">
      <section className="card w-full max-w-md">
        <BrandLogo />
        <h1 className="mt-6 text-3xl font-bold">
          <Tx text="Accept Owner invitation" />
        </h1>
        <p className="muted mt-2">
          {" "}
          <Tx text="Create or confirm your login to join this gym as an equal-authority Owner." />{" "}
        </p>
        {token ? (
          <OwnerInvitationForm token={token} />
        ) : (
          <p className="error mt-5">
            <Tx text="This invitation link is incomplete." />
          </p>
        )}
      </section>
    </main>
  );
}
