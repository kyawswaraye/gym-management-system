export type ApiErrorBody = {
  success: false;
  error: {
    code: string;
    message: string;
    fields?: Record<string, string[] | string>;
  };
  request_id?: string;
};

export type GymSummary = {
  id?: string;
  gym_id?: string;
  gym_membership_id?: string;
  name?: string;
  display_name?: string;
  status?: string;
  currency?: string;
  timezone?: string;
  gym?: { code?: string; name?: string };
};

export type CurrentUser = {
  user: {
    id: string;
    email: string;
    first_name: string;
    last_name: string;
  };
  gym: GymSummary | null;
  gym_membership: {
    id: string;
    status: string;
    is_owner: boolean;
    roles: Array<{ key?: string; name?: string }>;
  } | null;
  permissions: string[];
  feature_codes?: string[];
  authz_version: number;
};

export type Member = {
  id: string;
  member_number: string;
  first_name: string;
  last_name: string;
  display_name: string;
  phone: string;
  email: string;
  join_date: string;
  notes?: string;
  status: string;
};

export type MembershipPlan = {
  id: string;
  code: string;
  name: string;
  duration_value: number;
  duration_unit: string;
  price: string;
  currency: string;
  is_active: boolean;
};

export type Membership = {
  id: string;
  member: string;
  member_name: string;
  plan_name: string;
  start_date: string;
  expiry_date: string;
  final_amount: string;
  paid_amount: string;
  outstanding_amount: string;
  currency: string;
  payment_status: string;
  status: string;
  revenue_source: string;
};

export type Payment = {
  id: string;
  member: string;
  member_name: string;
  membership: string | null;
  purpose: string;
  description: string;
  amount: string;
  currency: string;
  method: string;
  business_date: string;
  received_at: string;
  status: string;
  receipt_number?: string;
};

export type DashboardData = {
  range: "today" | "month";
  income: string;
  revenue_sources: { source: string; amount: string }[];
  pending_payments: { member_count: number; amount: string };
  pending_approvals: number;
  expiring_members: number;
  attendance: { available: boolean; today: number | null };
  currency: string;
};

export type ApiEnvelope<T> = {
  success: true;
  data: T;
  message?: string;
};
