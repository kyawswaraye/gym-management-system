import {
  createSecureStore,
  INSTALLATION_SERVICE,
  REFRESH_SERVICE,
  type KeychainPort,
} from "./secureStore";

function port(
  hardwareFails = false,
): KeychainPort & { writes: Array<Record<string, unknown>> } {
  const items = new Map<
    string,
    { password: string; options?: Record<string, unknown> }
  >();
  const writes: Array<Record<string, unknown>> = [];
  const adapter: KeychainPort & { writes: Array<Record<string, unknown>> } = {
    writes,
    ACCESSIBLE: {
      WHEN_UNLOCKED_THIS_DEVICE_ONLY: "AccessibleWhenUnlockedThisDeviceOnly",
    },
    ACCESS_CONTROL: {
      BIOMETRY_CURRENT_SET_OR_DEVICE_PASSCODE:
        "BiometryCurrentSetOrDevicePasscode",
    },
    SECURITY_LEVEL: { SECURE_HARDWARE: 1, SECURE_SOFTWARE: 0 },
    async setGenericPassword(_username, password, options) {
      writes.push({ password, ...options });
      if (hardwareFails && options?.securityLevel === 1) {
        throw new Error("StrongBox is unavailable.");
      }
      items.set(options?.service ?? "", { password, options });
      return { service: options?.service };
    },
    async getGenericPassword(options) {
      const item = items.get(options?.service ?? "");
      return item ? { username: "item", password: item.password } : false;
    },
    async resetGenericPassword(options) {
      items.delete(options?.service ?? "");
      return true;
    },
  };
  return adapter;
}

describe("secure storage", () => {
  it("stores the refresh token for this device in hardware-backed storage", async () => {
    const keychain = port();
    const store = createSecureStore(keychain);
    await store.setRefreshToken("refresh-secret");
    expect(await store.getRefreshToken()).toBe("refresh-secret");
    expect(keychain.writes[0]).toMatchObject({
      service: REFRESH_SERVICE,
      accessible: "AccessibleWhenUnlockedThisDeviceOnly",
      securityLevel: 1,
    });
  });

  it("falls back when hardware-backed storage is unavailable", async () => {
    const keychain = port(true);
    const store = createSecureStore(keychain);
    await store.setInstallationId("11111111-1111-4111-8111-111111111111");
    expect(await store.getInstallationId()).toBe(
      "11111111-1111-4111-8111-111111111111",
    );
    expect(keychain.writes.map((write) => write.securityLevel)).toEqual([1, 0]);
    expect(keychain.writes[1]?.service).toBe(INSTALLATION_SERVICE);
  });

  it("keeps the language choice when the session is deleted", async () => {
    const keychain = port();
    const store = createSecureStore(keychain);
    await store.setLocale("mm");
    await store.setRefreshToken("refresh-secret");
    await store.resetAll();
    expect(await store.getLocale()).toBe("mm");
    expect(await store.getRefreshToken()).toBeNull();
  });
});
