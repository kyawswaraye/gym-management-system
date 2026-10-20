export type AuthStackParamList = {
  Login: undefined;
};

export type HomeStackParamList = {
  Dashboard: undefined;
};

export type ScheduleStackParamList = {
  Schedule: undefined;
};

export type CheckInStackParamList = {
  Scan: undefined;
};

export type InboxStackParamList = {
  Inbox: undefined;
};

export type AccountStackParamList = {
  Account: undefined;
  Profile: undefined;
  Membership: undefined;
  Payments: undefined;
  CheckIns: undefined;
  Packages: undefined;
  NotificationSettings: undefined;
  Settings: undefined;
  Legal: { document: "privacy" | "terms" };
};

export type MainTabParamList = {
  Home: undefined;
  Schedule: undefined;
  CheckIn: undefined;
  Inbox: undefined;
  Account: undefined;
};
