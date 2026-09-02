import type { Metadata } from "next";

export const metadata: Metadata = { title: "Dashboard" };

export default function DashboardPage() {
  return (
    <section aria-labelledby="dashboard-title">
      <h1 id="dashboard-title" className="text-3xl font-bold">
        Dashboard
      </h1>
      <p className="muted mt-2">
        The secure tenant workspace is ready. Business features begin in later
        approved phases.
      </p>
      <div className="mt-6 rounded-xl border border-dashed border-slate-300 bg-white p-6">
        <h2 className="font-bold">Phase 1A foundation</h2>
        <p className="muted mt-2">
          Authentication, gym selection, and the protected application shell are
          available.
        </p>
      </div>
    </section>
  );
}
