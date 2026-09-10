import type { Metadata } from "next";

import { OwnerDashboard } from "@/features/operations/owner-dashboard";

export const metadata: Metadata = { title: "Dashboard" };

export default function DashboardPage() {
  return (
    <section aria-labelledby="dashboard-title">
      <h1 className="text-3xl font-bold" id="dashboard-title">
        Owner dashboard
      </h1>
      <p className="muted mb-6 mt-2">
        Revenue, outstanding membership payments, and expiring members.
      </p>
      <OwnerDashboard />
    </section>
  );
}
