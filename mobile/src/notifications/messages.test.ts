import type { ApiClient } from "../api/client";
import { createMemberApi } from "../api/memberApi";
import {
  noticeFromRemoteMessage,
  rememberNotice,
  rotatedTokenUpdate,
} from "./messages";

const notificationId = "11111111-1111-4111-8111-111111111111";

describe("push message routing", () => {
  it("opens only a notification id from the data payload", () => {
    const notice = noticeFromRemoteMessage({
      data: {
        notification_id: notificationId,
        gym_id: "other-gym",
        url: "https://evil.example/phish",
      },
      notification: { title: "Promotion", body: "10% off" },
    });
    expect(notice).toEqual({
      notificationId,
      title: "Promotion",
      body: "10% off",
    });
    expect(
      noticeFromRemoteMessage({ data: { notification_id: "note-1" } }),
    ).toBeNull();
    expect(noticeFromRemoteMessage(null)).toBeNull();
  });

  it("shows a foreground notice once", () => {
    const seen = new Set<string>();
    const notice = {
      notificationId,
      title: "Promotion",
      body: "10% off",
    };
    expect(rememberNotice(seen, notice)).toBe(true);
    expect(rememberNotice(seen, notice)).toBe(false);
  });

  it("updates the saved installation and does not register a second device", () => {
    expect(
      rotatedTokenUpdate({
        signedIn: false,
        pushConsent: true,
        deviceId: "device-1",
        token: "rotated",
        appVersion: "0.1.0",
        osVersion: "14",
      }),
    ).toBeNull();
    expect(
      rotatedTokenUpdate({
        signedIn: true,
        pushConsent: false,
        deviceId: "device-1",
        token: "rotated",
        appVersion: "0.1.0",
        osVersion: "14",
      }),
    ).toBeNull();
    expect(
      rotatedTokenUpdate({
        signedIn: true,
        pushConsent: true,
        deviceId: null,
        token: "rotated",
        appVersion: "0.1.0",
        osVersion: "14",
      }),
    ).toBeNull();
    const update = rotatedTokenUpdate({
      signedIn: true,
      pushConsent: true,
      deviceId: "device-1",
      token: "rotated",
      appVersion: "0.1.0",
      osVersion: "14",
    });
    expect(update?.deviceId).toBe("device-1");
    expect(update?.body.push_token).toBe("rotated");
    expect(update?.body.push_consent).toBe(true);
    expect(update?.body).not.toHaveProperty("installation_id");
  });

  it("asks the inbox for active rows", async () => {
    const request = jest.fn(async () => ({
      data: [],
      meta: { has_more: false, next_after: null },
    }));
    const api = createMemberApi({ request } as unknown as ApiClient);
    await api.notifications();
    expect(request).toHaveBeenCalledWith("/api/v1/member/notifications/", {
      query: { limit: 25, after: undefined, view: "active" },
    });
  });
});
