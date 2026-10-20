import { buildDeviceRegistration, buildDeviceUpdate } from "./device";

const installationId = "11111111-1111-4111-8111-111111111111";

describe("device consent", () => {
  it("registers a consented device with its token and omits the token when consent is off", () => {
    expect(
      buildDeviceRegistration({
        platform: "ANDROID",
        installationId,
        pushConsent: true,
        pushToken: "fcm-token",
        appVersion: "0.1.0",
        osVersion: "34",
      }),
    ).toMatchObject({
      push_consent: true,
      push_token: "fcm-token",
      installation_id: installationId,
    });

    const off = buildDeviceRegistration({
      platform: "IOS",
      installationId,
      pushConsent: false,
      pushToken: null,
      appVersion: "0.1.0",
      osVersion: "16.0",
    });
    expect(off.push_consent).toBe(false);
    expect(off.push_token).toBeUndefined();
  });

  it("clears the token when consent is turned off", () => {
    expect(
      buildDeviceUpdate({
        pushConsent: false,
        pushToken: null,
        appVersion: "0.1.0",
        osVersion: "34",
      }),
    ).toMatchObject({ push_consent: false, push_token: null });
  });

  it("rejects consent without a token and a token without consent", () => {
    expect(() =>
      buildDeviceRegistration({
        platform: "ANDROID",
        installationId,
        pushConsent: true,
        pushToken: null,
        appVersion: "0.1.0",
        osVersion: "34",
      }),
    ).toThrow(/turning push on/i);
    expect(() =>
      buildDeviceUpdate({
        pushConsent: false,
        pushToken: "fcm-token",
        appVersion: "0.1.0",
        osVersion: "34",
      }),
    ).toThrow(/cannot be stored/);
  });
});
