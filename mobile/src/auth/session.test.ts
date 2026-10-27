import { createApiClient, type FetchLike } from "../api/client";
import { ApiError } from "../api/errors";
import { createTokenMemory } from "./memory";
import { createSession } from "./session";
import type { SecureStore } from "./secureStore";

function memoryStore(
  initial: Partial<Record<string, string>> = {},
): SecureStore {
  const values = { ...initial };
  return {
    getRefreshToken: async () => values.refresh ?? null,
    setRefreshToken: async (token) => {
      values.refresh = token;
    },
    getInstallationId: async () => values.installation ?? null,
    setInstallationId: async (id) => {
      values.installation = id;
    },
    getDeviceId: async () => values.device ?? null,
    setDeviceId: async (id) => {
      values.device = id;
    },
    getBiometricGate: async () => false,
    setBiometricGate: async () => undefined,
    getLocale: async () => (values.locale === "mm" ? "mm" : "en"),
    setLocale: async (locale) => {
      values.locale = locale;
    },
    resetAll: async () => {
      delete values.refresh;
      delete values.installation;
      delete values.device;
    },
  };
}

function json(status: number, body: unknown) {
  return {
    status,
    headers: { get: () => null },
    text: async () => (status === 204 ? "" : JSON.stringify(body)),
  };
}

describe("session cleanup", () => {
  it("logs out on the server before wiping the phone and the query cache", async () => {
    const order: string[] = [];
    const fetchImpl: FetchLike = jest.fn(async (url) => {
      order.push(url);
      if (url.endsWith("/devices/device-1/")) {
        return json(204, null);
      }
      return json(204, null);
    });
    const memory = createTokenMemory();
    memory.setAccessToken("access-1");
    const store = memoryStore({
      refresh: "refresh-1",
      device: "device-1",
      installation: "install-1",
    });
    const cleared = jest.fn();
    const session = createSession({
      api: createApiClient({
        baseUrl: "http://10.0.2.2:8000",
        fetchImpl,
        getAccessToken: () => memory.getAccessToken(),
        setAccessToken: (token) => memory.setAccessToken(token),
        getRefreshToken: () => store.getRefreshToken(),
        setRefreshToken: (token) => store.setRefreshToken(token),
        onSessionRevoked: async () => session.wipe(),
      }),
      store,
      memory,
      clearCache: cleared,
    });
    const result = await session.logout();
    expect(result.serverConfirmed).toBe(true);
    expect(order[0]).toContain("/api/v1/member/devices/device-1/");
    expect(order[1]).toContain("/api/v1/member-auth/logout/");
    expect(memory.getAccessToken()).toBeNull();
    expect(await store.getRefreshToken()).toBeNull();
    expect(await store.getInstallationId()).toBeNull();
    expect(cleared).toHaveBeenCalledTimes(1);
  });

  it("wipes a revoked refresh without another logout", async () => {
    const fetchImpl: FetchLike = jest.fn(async () =>
      json(401, {
        success: false,
        error: {
          code: "AUTHENTICATION_FAILED",
          message: "Authentication credentials are invalid.",
          fields: {},
        },
        request_id: "req-1",
      }),
    );
    const memory = createTokenMemory();
    const store = memoryStore({ refresh: "refresh-1" });
    const cleared = jest.fn();
    let session = createSession({
      api: createApiClient({
        baseUrl: "https://api.example.com",
        fetchImpl,
        getAccessToken: () => memory.getAccessToken(),
        setAccessToken: (token) => memory.setAccessToken(token),
        getRefreshToken: () => store.getRefreshToken(),
        setRefreshToken: (token) => store.setRefreshToken(token),
        onSessionRevoked: async () => {
          await session.wipe();
        },
      }),
      store,
      memory,
      clearCache: cleared,
    });
    await expect(session.bootstrap()).resolves.toBe("anonymous");
    expect(await store.getRefreshToken()).toBeNull();
    expect(cleared).toHaveBeenCalled();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("does not keep an access token when secure storage fails during login", async () => {
    const fetchImpl: FetchLike = jest.fn(async () =>
      json(200, {
        success: true,
        message: "",
        data: {
          access_token: "access-1",
          access_expires_at: "2026-10-20T00:05:00Z",
          refresh_token: "refresh-1",
          refresh_expires_at: "2026-11-20T00:00:00Z",
          member_id: "member-1",
          gym_id: "gym-1",
        },
      }),
    );
    const memory = createTokenMemory();
    const store = memoryStore();
    store.setRefreshToken = async () => {
      throw new Error("Keystore unavailable.");
    };
    const session = createSession({
      api: createApiClient({
        baseUrl: "http://10.0.2.2:8000",
        fetchImpl,
        getAccessToken: () => memory.getAccessToken(),
        setAccessToken: (token) => memory.setAccessToken(token),
        getRefreshToken: () => store.getRefreshToken(),
        setRefreshToken: (token) => store.setRefreshToken(token),
        onSessionRevoked: async () => undefined,
      }),
      store,
      memory,
      clearCache: () => undefined,
    });
    await expect(session.login("google-id-token")).rejects.toThrow(
      "Keystore unavailable.",
    );
    expect(memory.getAccessToken()).toBeNull();
  });

  it("surfaces API errors", () => {
    const error = new ApiError(
      400,
      "INVALID_REQUEST",
      "The request could not be processed.",
    );
    expect(error.code).toBe("INVALID_REQUEST");
  });
});
