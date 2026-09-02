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

export type ApiEnvelope<T> = {
  success: true;
  data: T;
  message?: string;
};
