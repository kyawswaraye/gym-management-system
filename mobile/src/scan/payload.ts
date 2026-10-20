export const CHECK_IN_PAYLOAD_PREFIX = "gms-checkin:v1:";

export type CheckInPayloadResult =
  | { ok: true; payload: string }
  | { ok: false; reason: "empty" | "unrecognized" };

export function readCheckInPayload(raw: string): CheckInPayloadResult {
  const payload = raw.trim();
  if (!payload) {
    return { ok: false, reason: "empty" };
  }
  if (!payload.startsWith(CHECK_IN_PAYLOAD_PREFIX)) {
    return { ok: false, reason: "unrecognized" };
  }
  if (payload.length <= CHECK_IN_PAYLOAD_PREFIX.length || /\s/.test(payload)) {
    return { ok: false, reason: "unrecognized" };
  }
  return { ok: true, payload };
}
