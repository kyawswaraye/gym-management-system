export const queryKeys = {
  me: ["member", "me"] as const,
  dashboard: ["member", "dashboard"] as const,
  membership: ["member", "membership"] as const,
  payments: ["member", "payments"] as const,
  checkIns: ["member", "check-ins"] as const,
  packages: ["member", "packages"] as const,
  schedule: (from: string, to: string) =>
    ["member", "schedule", from, to] as const,
  notifications: ["member", "notifications"] as const,
  photo: (id: string) => ["member", "photo", id] as const,
};
