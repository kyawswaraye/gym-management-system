import type { Metadata } from "next";

export const metadata: Metadata = { title: "Super admin" };

export default function SuperAdminDashboardPage() {
  return (
    <section aria-labelledby="platform-title">
      <h1 id="platform-title" className="text-3xl font-bold">
        Super admin
      </h1>
      <p className="muted mt-2">
        This protected placeholder contains no administration business features.
        Provisioning is Phase 1B.
      </p>
    </section>
  );
}
