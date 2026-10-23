import { redirect } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { ClassesManager } from "@/features/pt/classes-manager";
import {
  canOfferUnmeteredBooking,
  canOpenClasses,
  hasPermission,
  holdsProtectedManagement,
  tenantHomePath,
} from "@/lib/auth/authorization";
import { requireTenantSession } from "@/lib/auth/session";
import { todayInTimezone } from "@/lib/date";

export default async function ClassesPage() {
  const session = await requireTenantSession();
  if (!canOpenClasses(session)) redirect(tenantHomePath(session));
  const canManage = hasPermission(session, "classes.manage");

  return (
    <section aria-labelledby="classes-title">
      <PageHeader
        description="Maintain class types, weekly schedules, occurrences, and bookings. Capacity, attendance, no-show, and cutoff cancellation follow the published class rules."
        eyebrow="Personal training"
        title="Classes"
        titleId="classes-title"
      />
      <ClassesManager
        canAttend={hasPermission(session, "classes.attendance")}
        canBook={hasPermission(session, "classes.book")}
        canLateCancel={canManage && holdsProtectedManagement(session)}
        canManage={canManage}
        canUnmetered={canOfferUnmeteredBooking(session)}
        today={todayInTimezone(session.gym?.timezone ?? "UTC")}
      />
    </section>
  );
}
