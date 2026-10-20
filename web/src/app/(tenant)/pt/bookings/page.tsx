import { redirect } from "next/navigation";

import { BookingsManager } from "@/features/pt/bookings-manager";
import {
  canOfferUnmeteredBooking,
  canOpenPtSchedule,
  hasPermission,
  tenantHomePath,
} from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";
import { todayInTimezone } from "@/lib/date";

export default async function PtBookingsPage() {
  const session = await requireTenantSession();
  if (!canOpenPtSchedule(session)) redirect(tenantHomePath(session));

  return (
    <section aria-labelledby="pt-bookings-title">
      <h1 className="text-2xl font-bold" id="pt-bookings-title">
        PT bookings
      </h1>
      <p className="muted mb-6 mt-2">
        Work a gym-local calendar of personal-training sessions. Attendance and
        no-show consume a package unless a protected manager records an
        unmetered reason.
      </p>
      <BookingsManager
        canManage={hasPermission(session, "pt.schedule.manage")}
        canUnmetered={canOfferUnmeteredBooking(session)}
        timeZone={session.gym?.timezone ?? "UTC"}
        today={todayInTimezone(session.gym?.timezone ?? "UTC")}
      />
    </section>
  );
}
