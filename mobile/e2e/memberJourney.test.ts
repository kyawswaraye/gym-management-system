import { createApiClient, type FetchLike } from "../src/api/client";
import { createMemberApi } from "../src/api/memberApi";
import { createTokenMemory } from "../src/auth/memory";
import { createSession } from "../src/auth/session";
import type { SecureStore } from "../src/auth/secureStore";

function json(status: number, body: unknown) {
  return {
    status,
    headers: { get: () => null },
    text: async () => (status === 204 ? "" : JSON.stringify(body)),
  };
}

describe("member journey", () => {
  it("signs in, reads the profile, books a class once, and signs out", async () => {
    const bookings: string[] = [];
    const fetchImpl: FetchLike = jest.fn(async (url, init) => {
      if (url.endsWith("/api/v1/member-auth/login/")) {
        return json(200, {
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
        });
      }
      if (url.endsWith("/api/v1/member/me/")) {
        return json(200, {
          success: true,
          message: "",
          data: { id: "member-1", first_name: "Ana" },
        });
      }
      if (url.includes("/api/v1/member/class-bookings/")) {
        bookings.push(init.headers["Idempotency-Key"] ?? "");
        return json(201, {
          success: true,
          message: "",
          data: { id: "booking-1", kind: "CLASS", status: "BOOKED" },
        });
      }
      if (url.endsWith("/api/v1/member-auth/logout/")) {
        return json(204, null);
      }
      return json(404, {
        success: false,
        error: { code: "NOT_FOUND", message: "Not found.", fields: {} },
        request_id: "req-missing",
      });
    });
    const values: Record<string, string> = {};
    const store: SecureStore = {
      getRefreshToken: async () => values.refresh ?? null,
      setRefreshToken: async (token) => {
        values.refresh = token;
      },
      getInstallationId: async () => null,
      setInstallationId: async () => undefined,
      getDeviceId: async () => null,
      setDeviceId: async () => undefined,
      getBiometricGate: async () => false,
      setBiometricGate: async () => undefined,
      resetAll: async () => {
        delete values.refresh;
      },
    };
    const memory = createTokenMemory();
    const cleared = jest.fn();
    const client = createApiClient({
      baseUrl: "http://10.0.2.2:8000",
      fetchImpl,
      getAccessToken: () => memory.getAccessToken(),
      setAccessToken: (token) => memory.setAccessToken(token),
      getRefreshToken: () => store.getRefreshToken(),
      setRefreshToken: (token) => store.setRefreshToken(token),
      onSessionRevoked: async () => undefined,
    });
    const session = createSession({
      api: client,
      store,
      memory,
      clearCache: cleared,
    });
    const api = createMemberApi(client);
    await session.login("member@example.com", "secret");
    const me = await api.me();
    expect(me).toMatchObject({ first_name: "Ana" });
    const key = "journey-key-123456";
    await api.bookClass(
      { occurrence_id: "occurrence-1", member_service_package_id: "package-1" },
      key,
    );
    await api.bookClass(
      { occurrence_id: "occurrence-1", member_service_package_id: "package-1" },
      key,
    );
    expect(bookings).toEqual([key, key]);
    await session.logout();
    expect(memory.getAccessToken()).toBeNull();
    expect(await store.getRefreshToken()).toBeNull();
    expect(cleared).toHaveBeenCalled();
  });
});
