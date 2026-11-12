import type { Schemas } from "../api/memberApi";
import { buildDeviceUpdate } from "./device";

const NOTIFICATION_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export type RemoteNotice = {
  notificationId: string;
  title: string;
  body: string;
};

type RemoteMessage = {
  data?: Record<string, unknown> | null;
  notification?: { title?: string; body?: string } | null;
};

export function noticeFromRemoteMessage(
  message: RemoteMessage | null | undefined,
): RemoteNotice | null {
  const raw = message?.data?.notification_id;
  if (typeof raw !== "string" || !NOTIFICATION_ID.test(raw)) {
    return null;
  }
  const title = message?.notification?.title?.trim() || "Notification";
  const body = message?.notification?.body?.trim() || "";
  return { notificationId: raw.toLowerCase(), title, body };
}

export function rememberNotice(
  seen: Set<string>,
  notice: RemoteNotice,
): boolean {
  if (seen.has(notice.notificationId)) {
    return false;
  }
  seen.add(notice.notificationId);
  return true;
}

export function rotatedTokenUpdate(input: {
  signedIn: boolean;
  pushConsent: boolean;
  deviceId: string | null;
  token: string | null;
  appVersion: string;
  osVersion: string;
}): {
  deviceId: string;
  body: Schemas["PatchedMemberDevicePatchRequest"];
} | null {
  if (
    !input.signedIn ||
    !input.pushConsent ||
    !input.deviceId ||
    !input.token
  ) {
    return null;
  }
  return {
    deviceId: input.deviceId,
    body: buildDeviceUpdate({
      pushConsent: true,
      pushToken: input.token,
      appVersion: input.appVersion,
      osVersion: input.osVersion,
    }),
  };
}
