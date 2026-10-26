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
  const timeZone = session.gym?.timezone ?? "UTC";

  return (
    <section aria-labelledby="classes-title">
      <PageHeader
        description="Publish class types, repeat them through the week, and book members onto each occurrence."
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
        timeZone={timeZone}
        today={todayInTimezone(timeZone)}
      />
    </section>
  );
}
