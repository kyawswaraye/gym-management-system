import * as Keychain from "react-native-keychain";

export const REFRESH_SERVICE = "com.gymplatform.member.refresh";
export const INSTALLATION_SERVICE = "com.gymplatform.member.installation";
export const DEVICE_SERVICE = "com.gymplatform.member.device";
export const PREFERENCE_SERVICE = "com.gymplatform.member.preferences";
export const LOCALE_SERVICE = "com.gymplatform.member.locale";

export type SecureWriteOptions = {
  service: string;
  accessible?: string;
  securityLevel?: number;
  accessControl?: string;
};

export type KeychainPort = {
  setGenericPassword(
    username: string,
    password: string,
    options?: SecureWriteOptions,
  ): Promise<false | { service?: string }>;
  getGenericPassword(options?: {
    service: string;
    accessControl?: string;
  }): Promise<false | { username: string; password: string }>;
  resetGenericPassword(options?: { service: string }): Promise<boolean>;
  ACCESSIBLE: { WHEN_UNLOCKED_THIS_DEVICE_ONLY: string };
  ACCESS_CONTROL: { BIOMETRY_CURRENT_SET_OR_DEVICE_PASSCODE: string };
  SECURITY_LEVEL: { SECURE_HARDWARE: number; SECURE_SOFTWARE: number };
};

export type SecureStore = {
  getRefreshToken(): Promise<string | null>;
  setRefreshToken(token: string): Promise<void>;
  getInstallationId(): Promise<string | null>;
  setInstallationId(id: string): Promise<void>;
  getDeviceId(): Promise<string | null>;
  setDeviceId(id: string): Promise<void>;
  getBiometricGate(): Promise<boolean>;
  setBiometricGate(enabled: boolean): Promise<void>;
  getLocale(): Promise<"en" | "mm">;
  setLocale(locale: "en" | "mm"): Promise<void>;
  resetAll(): Promise<void>;
};

export function createSecureStore(
  port: KeychainPort = Keychain as unknown as KeychainPort,
): SecureStore {
  async function read(
    service: string,
    biometric = false,
  ): Promise<string | null> {
    const result = await port.getGenericPassword({
      service,
      accessControl: biometric
        ? port.ACCESS_CONTROL.BIOMETRY_CURRENT_SET_OR_DEVICE_PASSCODE
        : undefined,
    });
    if (!result) {
      return null;
    }
    return result.password;
  }

  async function write(
    service: string,
    value: string,
    biometric = false,
  ): Promise<void> {
    const accessible = port.ACCESSIBLE.WHEN_UNLOCKED_THIS_DEVICE_ONLY;
    const accessControl = biometric
      ? port.ACCESS_CONTROL.BIOMETRY_CURRENT_SET_OR_DEVICE_PASSCODE
      : undefined;
    try {
      const saved = await port.setGenericPassword("item", value, {
        service,
        accessible,
        accessControl,
        securityLevel: port.SECURITY_LEVEL.SECURE_HARDWARE,
      });
      if (saved === false) {
        throw new Error("Secure storage rejected the item.");
      }
    } catch (error) {
      const saved = await port.setGenericPassword("item", value, {
        service,
        accessible,
        accessControl,
        securityLevel: port.SECURITY_LEVEL.SECURE_SOFTWARE,
      });
      if (saved === false) {
        throw error instanceof Error
          ? error
          : new Error("Secure storage rejected the item.");
      }
    }
  }

  return {
    async getRefreshToken() {
      const biometric = (await read(PREFERENCE_SERVICE)) === "1";
      return read(REFRESH_SERVICE, biometric);
    },
    setRefreshToken(token) {
      return write(REFRESH_SERVICE, token, false).then(async () => {
        const biometric = (await read(PREFERENCE_SERVICE)) === "1";
        if (biometric) {
          await write(REFRESH_SERVICE, token, true);
        }
      });
    },
    getInstallationId: () => read(INSTALLATION_SERVICE),
    setInstallationId: (id) => write(INSTALLATION_SERVICE, id),
    getDeviceId: () => read(DEVICE_SERVICE),
    setDeviceId: (id) => write(DEVICE_SERVICE, id),
    getBiometricGate: async () => (await read(PREFERENCE_SERVICE)) === "1",
    async setBiometricGate(enabled) {
      await write(PREFERENCE_SERVICE, enabled ? "1" : "0");
      const refresh = await read(REFRESH_SERVICE);
      if (refresh) {
        await write(REFRESH_SERVICE, refresh, enabled);
      }
    },
    async getLocale() {
      const value = await read(LOCALE_SERVICE);
      return value === "mm" ? "mm" : "en";
    },
    setLocale(locale) {
      return write(LOCALE_SERVICE, locale);
    },
    async resetAll() {
      await port.resetGenericPassword({ service: REFRESH_SERVICE });
      await port.resetGenericPassword({ service: INSTALLATION_SERVICE });
      await port.resetGenericPassword({ service: DEVICE_SERVICE });
      await port.resetGenericPassword({ service: PREFERENCE_SERVICE });
    },
  };
}
