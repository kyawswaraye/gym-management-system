import type { Schemas } from "../api/memberApi";

export type DeviceDraft = {
  platform: Schemas["PlatformEnum"];
  installationId: string;
  pushConsent: boolean;
  pushToken: string | null;
  appVersion: string;
  osVersion: string;
  deviceName?: string | null;
};

export function buildDeviceRegistration(
  draft: DeviceDraft,
): Schemas["MemberDeviceWriteRequest"] {
  assertConsent(draft.pushConsent, draft.pushToken);
  const body: Schemas["MemberDeviceWriteRequest"] = {
    platform: draft.platform,
    installation_id: draft.installationId,
    push_consent: draft.pushConsent,
    app_version: bounded(draft.appVersion, 32, "app version"),
    os_version: bounded(draft.osVersion, 32, "OS version"),
  };
  if (draft.deviceName) {
    body.device_name = bounded(draft.deviceName, 64, "device name");
  }
  if (draft.pushConsent && draft.pushToken) {
    body.push_token = draft.pushToken;
  }
  return body;
}

export function buildDeviceUpdate(input: {
  pushConsent: boolean;
  pushToken: string | null;
  appVersion: string;
  osVersion: string;
}): Schemas["PatchedMemberDevicePatchRequest"] {
  assertConsent(input.pushConsent, input.pushToken);
  const body: Schemas["PatchedMemberDevicePatchRequest"] = {
    push_consent: input.pushConsent,
    app_version: bounded(input.appVersion, 32, "app version"),
    os_version: bounded(input.osVersion, 32, "OS version"),
  };
  if (input.pushConsent && input.pushToken) {
    body.push_token = input.pushToken;
  } else {
    body.push_token = null;
  }
  return body;
}

function assertConsent(pushConsent: boolean, pushToken: string | null): void {
  if (pushConsent && !pushToken) {
    throw new Error(
      "Allow notifications on this phone before turning push on.",
    );
  }
  if (!pushConsent && pushToken) {
    throw new Error("A push token cannot be stored when push is off.");
  }
}

function bounded(value: string, max: number, label: string): string {
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > max) {
    throw new Error(`The ${label} is not valid.`);
  }
  return trimmed;
}
