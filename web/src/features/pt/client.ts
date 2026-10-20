import {
  operationGet,
  operationList,
  operationMutation,
  OperationError,
} from "@/features/operations/client";
import type { CollectionPage } from "@/features/operations/client";
import type {
  AssignmentCancelRequest,
  AssignmentCreateRequest,
  AssignmentEndRequest,
  AssignedMember,
  ClassBooking,
  ClassBookingCancelRequest,
  ClassBookingWriteRequest,
  ClassOccurrence,
  ClassOccurrenceCancelRequest,
  ClassSchedule,
  ClassSchedulePatchRequest,
  ClassScheduleWriteRequest,
  ClassType,
  ClassTypePatchRequest,
  ClassTypeWriteRequest,
  LedgerAdjustRequest,
  MemberPackageGrantRequest,
  MemberPackageGrantResult,
  MemberServicePackage,
  PTBooking,
  PTBookingCancelRequest,
  PTBookingWriteRequest,
  PTCommission,
  PTCommissionDetail,
  PTCommissionPaymentResult,
  PTCommissionPaymentWriteRequest,
  PTSale,
  PTSaleApproval,
  PTSaleApproveRequest,
  PTSaleCancelRequest,
  PTSaleRejectRequest,
  PTSaleUpdateRequest,
  PTSaleVoidRequest,
  PTSaleVoidResult,
  PTSaleWriteRequest,
  ServicePackage,
  ServicePackageWriteRequest,
  ServiceSessionLedger,
  TrainerAssignment,
  TrainerDashboard,
  TrainerDashboardQuery,
  TrainerIdentity,
  TrainerSessionReport,
  DashboardRangePreset,
} from "@/types/api";

const RESOURCE_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function resourceId(value: string, label: string): string {
  const id = value.toLowerCase();
  if (!RESOURCE_ID.test(id))
    throw new OperationError(`${label} was not found.`);
  return id;
}

function query(values: Record<string, string | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value) params.set(key, value);
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

function listed<T>(path: string, values: Record<string, string | undefined>) {
  return operationList<T>(`${path}${query(values)}`);
}

function plain<T>(
  path: string,
  body: object,
  method: "POST" | "PATCH" = "POST",
): Promise<T> {
  return operationMutation<T>(path, body, method);
}

function command<T>(
  path: string,
  body: object,
  idempotencyKey: string,
  method: "POST" | "PATCH" = "POST",
): Promise<T> {
  return operationMutation<T>(path, body, method, idempotencyKey);
}

export type TrainerListQuery = { limit?: string; after?: string };
export type AssignmentListQuery = TrainerListQuery & {
  status?: string;
  trainer_staff_profile_id?: string;
  member_id?: string;
};
export type PackageListQuery = TrainerListQuery & {
  type?: string;
  is_active?: string;
};
export type GrantListQuery = TrainerListQuery & {
  member_id?: string;
  status?: string;
  package_type?: string;
};
export type SaleListQuery = TrainerListQuery & {
  status?: string;
  member_id?: string;
  trainer_staff_profile_id?: string;
  service_date_from?: string;
  service_date_to?: string;
};
export type BookingListQuery = TrainerListQuery & {
  status?: string;
  trainer_staff_profile_id?: string;
  member_id?: string;
  date_from?: string;
  date_to?: string;
  view?: string;
};
export type ScheduleListQuery = TrainerListQuery & {
  status?: string;
  trainer_staff_profile_id?: string;
  class_type_id?: string;
};
export type OccurrenceListQuery = TrainerListQuery & {
  status?: string;
  trainer_staff_profile_id?: string;
  date_from?: string;
  date_to?: string;
  view?: string;
};

export function listTrainers(
  values: TrainerListQuery,
): Promise<CollectionPage<TrainerIdentity>> {
  return listed<TrainerIdentity>("trainers/", values);
}

export function listAssignedMembers(
  trainerId: string,
  values: TrainerListQuery,
): Promise<CollectionPage<AssignedMember>> {
  return listed<AssignedMember>(
    `trainers/${resourceId(trainerId, "Trainer")}/assigned-members/`,
    values,
  );
}

export function listAssignments(
  values: AssignmentListQuery,
): Promise<CollectionPage<TrainerAssignment>> {
  return listed<TrainerAssignment>("trainer-assignments/", values);
}

export function createAssignment(
  body: AssignmentCreateRequest,
): Promise<TrainerAssignment> {
  return plain<TrainerAssignment>("trainer-assignments/", body);
}

export function endAssignment(
  assignmentId: string,
  body: AssignmentEndRequest,
): Promise<TrainerAssignment> {
  return plain<TrainerAssignment>(
    `trainer-assignments/${resourceId(assignmentId, "Assignment")}/end/`,
    body,
  );
}

export function cancelAssignment(
  assignmentId: string,
  body: AssignmentCancelRequest,
): Promise<TrainerAssignment> {
  return plain<TrainerAssignment>(
    `trainer-assignments/${resourceId(assignmentId, "Assignment")}/cancel/`,
    body,
  );
}

export function listServicePackages(
  values: PackageListQuery,
): Promise<CollectionPage<ServicePackage>> {
  return listed<ServicePackage>("service-packages/", values);
}

export function createServicePackage(
  body: ServicePackageWriteRequest,
): Promise<ServicePackage> {
  return plain<ServicePackage>("service-packages/", body);
}

export function listMemberPackages(
  values: GrantListQuery,
): Promise<CollectionPage<MemberServicePackage>> {
  return listed<MemberServicePackage>("member-service-packages/", values);
}

export function grantMemberPackage(
  body: MemberPackageGrantRequest,
  idempotencyKey: string,
): Promise<MemberPackageGrantResult> {
  return command<MemberPackageGrantResult>(
    "member-service-packages/",
    body,
    idempotencyKey,
  );
}

export function listPackageLedger(
  packageId: string,
  values: TrainerListQuery,
): Promise<CollectionPage<ServiceSessionLedger>> {
  return listed<ServiceSessionLedger>(
    `member-service-packages/${resourceId(packageId, "Member package")}/ledger/`,
    values,
  );
}

export function adjustPackageLedger(
  packageId: string,
  body: LedgerAdjustRequest,
  idempotencyKey: string,
): Promise<MemberPackageGrantResult> {
  return command<MemberPackageGrantResult>(
    `member-service-packages/${resourceId(packageId, "Member package")}/ledger/adjust/`,
    body,
    idempotencyKey,
  );
}

export function listPtSales(
  values: SaleListQuery,
): Promise<CollectionPage<PTSale>> {
  return listed<PTSale>("pt-sales/", values);
}

export function getPtSale(saleId: string): Promise<PTSale> {
  return operationGet<PTSale>(`pt-sales/${resourceId(saleId, "PT sale")}/`);
}

export function createPtSale(
  body: PTSaleWriteRequest,
  idempotencyKey: string,
): Promise<PTSale> {
  return command<PTSale>("pt-sales/", body, idempotencyKey);
}

export function updatePtSale(
  saleId: string,
  body: PTSaleUpdateRequest,
  idempotencyKey: string,
): Promise<PTSale> {
  return command<PTSale>(
    `pt-sales/${resourceId(saleId, "PT sale")}/`,
    body,
    idempotencyKey,
    "PATCH",
  );
}

export function submitPtSale(
  saleId: string,
  idempotencyKey: string,
): Promise<PTSale> {
  return command<PTSale>(
    `pt-sales/${resourceId(saleId, "PT sale")}/submit/`,
    {},
    idempotencyKey,
  );
}

export function approvePtSale(
  saleId: string,
  body: PTSaleApproveRequest,
  idempotencyKey: string,
): Promise<PTSaleApproval> {
  return command<PTSaleApproval>(
    `pt-sales/${resourceId(saleId, "PT sale")}/approve/`,
    body,
    idempotencyKey,
  );
}

export function rejectPtSale(
  saleId: string,
  body: PTSaleRejectRequest,
  idempotencyKey: string,
): Promise<PTSale> {
  return command<PTSale>(
    `pt-sales/${resourceId(saleId, "PT sale")}/reject/`,
    body,
    idempotencyKey,
  );
}

export function cancelPtSale(
  saleId: string,
  body: PTSaleCancelRequest,
  idempotencyKey: string,
): Promise<PTSale> {
  return command<PTSale>(
    `pt-sales/${resourceId(saleId, "PT sale")}/cancel/`,
    body,
    idempotencyKey,
  );
}

export function voidPtSale(
  saleId: string,
  body: PTSaleVoidRequest,
  idempotencyKey: string,
): Promise<PTSaleVoidResult> {
  return command<PTSaleVoidResult>(
    `pt-sales/${resourceId(saleId, "PT sale")}/void/`,
    body,
    idempotencyKey,
  );
}

export function listPtCommissions(
  values: SaleListQuery,
): Promise<CollectionPage<PTCommission>> {
  return listed<PTCommission>("pt-commissions/", values);
}

export function getPtCommission(
  commissionId: string,
): Promise<PTCommissionDetail> {
  return operationGet<PTCommissionDetail>(
    `pt-commissions/${resourceId(commissionId, "Commission")}/`,
  );
}

export function recordPtCommissionPayment(
  commissionId: string,
  body: PTCommissionPaymentWriteRequest,
  idempotencyKey: string,
): Promise<PTCommissionPaymentResult> {
  return command<PTCommissionPaymentResult>(
    `pt-commissions/${resourceId(commissionId, "Commission")}/record-payment/`,
    body,
    idempotencyKey,
  );
}

export function listPtBookings(
  values: BookingListQuery,
): Promise<CollectionPage<PTBooking>> {
  return listed<PTBooking>("pt-bookings/", values);
}

export function createPtBooking(
  body: PTBookingWriteRequest,
  idempotencyKey: string,
): Promise<PTBooking> {
  return command<PTBooking>("pt-bookings/", body, idempotencyKey);
}

export function attendPtBooking(
  bookingId: string,
  idempotencyKey: string,
): Promise<PTBooking> {
  return command<PTBooking>(
    `pt-bookings/${resourceId(bookingId, "Booking")}/attend/`,
    {},
    idempotencyKey,
  );
}

export function noShowPtBooking(
  bookingId: string,
  idempotencyKey: string,
): Promise<PTBooking> {
  return command<PTBooking>(
    `pt-bookings/${resourceId(bookingId, "Booking")}/no-show/`,
    {},
    idempotencyKey,
  );
}

export function cancelPtBooking(
  bookingId: string,
  body: PTBookingCancelRequest,
  idempotencyKey: string,
): Promise<PTBooking> {
  return command<PTBooking>(
    `pt-bookings/${resourceId(bookingId, "Booking")}/cancel/`,
    body,
    idempotencyKey,
  );
}

export function listClassTypes(
  values: PackageListQuery,
): Promise<CollectionPage<ClassType>> {
  return listed<ClassType>("class-types/", { ...values, type: undefined });
}

export function createClassType(
  body: ClassTypeWriteRequest,
): Promise<ClassType> {
  return plain<ClassType>("class-types/", body);
}

export function updateClassType(
  classTypeId: string,
  body: ClassTypePatchRequest,
): Promise<ClassType> {
  return plain<ClassType>(
    `class-types/${resourceId(classTypeId, "Class type")}/`,
    body,
    "PATCH",
  );
}

export function listClassSchedules(
  values: ScheduleListQuery,
): Promise<CollectionPage<ClassSchedule>> {
  return listed<ClassSchedule>("class-schedules/", values);
}

export function createClassSchedule(
  body: ClassScheduleWriteRequest,
  idempotencyKey: string,
): Promise<ClassSchedule> {
  return command<ClassSchedule>("class-schedules/", body, idempotencyKey);
}

export function updateClassSchedule(
  scheduleId: string,
  body: ClassSchedulePatchRequest,
  idempotencyKey: string,
): Promise<ClassSchedule> {
  return command<ClassSchedule>(
    `class-schedules/${resourceId(scheduleId, "Schedule")}/`,
    body,
    idempotencyKey,
    "PATCH",
  );
}

export function listClassOccurrences(
  values: OccurrenceListQuery,
): Promise<CollectionPage<ClassOccurrence>> {
  return listed<ClassOccurrence>("class-occurrences/", values);
}

export function cancelClassOccurrence(
  occurrenceId: string,
  body: ClassOccurrenceCancelRequest,
  idempotencyKey: string,
): Promise<ClassOccurrence> {
  return command<ClassOccurrence>(
    `class-occurrences/${resourceId(occurrenceId, "Occurrence")}/cancel/`,
    body,
    idempotencyKey,
  );
}

export function listClassBookings(
  values: BookingListQuery,
): Promise<CollectionPage<ClassBooking>> {
  return listed<ClassBooking>("class-bookings/", values);
}

export function createClassBooking(
  body: ClassBookingWriteRequest,
  idempotencyKey: string,
): Promise<ClassBooking> {
  return command<ClassBooking>("class-bookings/", body, idempotencyKey);
}

export function attendClassBooking(
  bookingId: string,
  idempotencyKey: string,
): Promise<ClassBooking> {
  return command<ClassBooking>(
    `class-bookings/${resourceId(bookingId, "Booking")}/attend/`,
    {},
    idempotencyKey,
  );
}

export function noShowClassBooking(
  bookingId: string,
  idempotencyKey: string,
): Promise<ClassBooking> {
  return command<ClassBooking>(
    `class-bookings/${resourceId(bookingId, "Booking")}/no-show/`,
    {},
    idempotencyKey,
  );
}

export function cancelClassBooking(
  bookingId: string,
  body: ClassBookingCancelRequest,
  idempotencyKey: string,
): Promise<ClassBooking> {
  return command<ClassBooking>(
    `class-bookings/${resourceId(bookingId, "Booking")}/cancel/`,
    body,
    idempotencyKey,
  );
}

const DASHBOARD_PRESETS = new Set<DashboardRangePreset>([
  "today",
  "this_week",
  "this_month",
  "last_month",
  "this_year",
]);
const REPORT_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const MAX_INCLUSIVE_DAYS = 366;

function realReportDate(value: string): boolean {
  const match = REPORT_DATE.exec(value);
  if (!match?.[1] || !match[2] || !match[3]) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (year < 1900 || year > 2099) return false;
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return (
    parsed.getUTCFullYear() === year &&
    parsed.getUTCMonth() === month - 1 &&
    parsed.getUTCDate() === day
  );
}

function inclusiveReportDays(dateFrom: string, dateTo: string): number {
  const start = REPORT_DATE.exec(dateFrom);
  const end = REPORT_DATE.exec(dateTo);
  if (
    !start?.[1] ||
    !start[2] ||
    !start[3] ||
    !end?.[1] ||
    !end[2] ||
    !end[3]
  ) {
    return 0;
  }
  return (
    (Date.UTC(Number(end[1]), Number(end[2]) - 1, Number(end[3])) -
      Date.UTC(Number(start[1]), Number(start[2]) - 1, Number(start[3]))) /
      86_400_000 +
    1
  );
}

function assertReportRange(dateFrom: string, dateTo: string): void {
  if (!realReportDate(dateFrom) || !realReportDate(dateTo)) {
    throw new OperationError("Enter a valid date.", {
      date_from: "Enter a valid date.",
    });
  }
  const days = inclusiveReportDays(dateFrom, dateTo);
  if (days < 1) {
    throw new OperationError("The end date cannot be before the start date.", {
      date_to: "The end date cannot be before the start date.",
    });
  }
  if (days > MAX_INCLUSIVE_DAYS) {
    throw new OperationError("A report range cannot exceed 366 days.", {
      date_to: "A report range cannot exceed 366 days.",
    });
  }
}

export function getTrainerDashboard(
  input: TrainerDashboardQuery,
): Promise<TrainerDashboard> {
  const params = new URLSearchParams();
  if ("range" in input) {
    if (!DASHBOARD_PRESETS.has(input.range)) {
      throw new OperationError("Choose a valid dashboard range.", {
        range: "Choose a valid dashboard range.",
      });
    }
    params.set("range", input.range);
  } else {
    assertReportRange(input.date_from, input.date_to);
    params.set("date_from", input.date_from);
    params.set("date_to", input.date_to);
  }
  return operationGet<TrainerDashboard>(
    `trainer/dashboard/?${params.toString()}`,
  );
}

export function getTrainerSessionReport(input: {
  date_from: string;
  date_to: string;
}): Promise<TrainerSessionReport> {
  assertReportRange(input.date_from, input.date_to);
  const params = new URLSearchParams({
    date_from: input.date_from,
    date_to: input.date_to,
  });
  return operationGet<TrainerSessionReport>(
    `trainer/reports/sessions/?${params.toString()}`,
  );
}
