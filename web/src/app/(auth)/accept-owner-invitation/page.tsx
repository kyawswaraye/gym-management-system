import { BrandLogo } from "@/components/brand-logo";
import { OwnerInvitationForm } from "@/features/auth/owner-invitation-form";

export default async function AcceptOwnerInvitationPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const { token = "" } = await searchParams;
  return (
    <section className="card w-full max-w-md">
      <BrandLogo />
      <h1 className="mt-6 text-2xl font-bold">Accept Owner invitation</h1>
      <p className="muted mt-2">
        Create or confirm your login to join this gym as an equal-authority
        Owner.
      </p>
      {token ? (
        <OwnerInvitationForm token={token} />
      ) : (
        <p className="error mt-5">This invitation link is incomplete.</p>
      )}
    </section>
  );
}
