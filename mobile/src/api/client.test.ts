import { createApiClient, BFF_SECRET_HEADER, type FetchLike } from "./client";
import { createMemberApi } from "./memberApi";
import { ApiError } from "./errors";

function json(
  status: number,
  body: unknown,
  headers: Record<string, string> = {},
) {
  return {
    status,
    headers: { get: (name: string) => headers[name.toLowerCase()] ?? null },
    text: async (): Promise<string> =>
      status === 204 ? "" : JSON.stringify(body),
  };
}

function token(access: string, refresh = "refresh-token-value") {
  return {
    success: true,
    message: "",
    data: {
      access_token: access,
      access_expires_at: "2026-10-20T00:05:00Z",
      refresh_token: refresh,
      refresh_expires_at: "2026-11-20T00:00:00Z",
      member_id: "member-1",
      gym_id: "gym-1",
    },
  };
}

describe("member API client", () => {
  it("refreshes once for concurrent expired access tokens and retries each call once", async () => {
    let refreshCalls = 0;
    const fetchImpl: FetchLike = jest.fn(async (url, init) => {
      if (url.endsWith("/api/v1/member-auth/refresh/")) {
        refreshCalls += 1;
        await new Promise<void>((resolve) => {
          setTimeout(resolve, 20);
        });
        return json(200, token("access-2", "refresh-2"));
      }
      const authorization = init.headers.Authorization;
      if (authorization === "Bearer access-1") {
        return json(401, {
          success: false,
          error: {
            code: "ACCESS_TOKEN_EXPIRED",
            message: "The access token has expired.",
            fields: {},
          },
          request_id: "req-expired",
        });
      }
      expect(init.headers[BFF_SECRET_HEADER]).toBeUndefined();
      return json(200, { success: true, data: { ok: url }, message: "" });
    });
    let access: string | null = "access-1";
    let refresh = "refresh-1";
    const client = createApiClient({
      baseUrl: "http://10.0.2.2:8000",
      fetchImpl,
      getAccessToken: () => access,
      setAccessToken: (tokenValue) => {
        access = tokenValue;
      },
      getRefreshToken: async () => refresh,
      setRefreshToken: async (tokenValue) => {
        refresh = tokenValue;
      },
      onSessionRevoked: jest.fn(async () => undefined),
    });
    const [first, second] = await Promise.all([
      client.request<{ ok: string }>("/api/v1/member/me/"),
      client.request<{ ok: string }>("/api/v1/member/dashboard/"),
    ]);
    expect(refreshCalls).toBe(1);
    expect(refresh).toBe("refresh-2");
    expect(access).toBe("access-2");
    expect(first?.data.ok).toContain("/api/v1/member/me/");
    expect(second?.data.ok).toContain("/api/v1/member/dashboard/");
  });

  it("wipes a revoked refresh session and does not retry forever", async () => {
    const revoked = jest.fn(async () => undefined);
    const fetchImpl: FetchLike = jest.fn(async (url) => {
      if (url.endsWith("/refresh/")) {
        return json(401, {
          success: false,
          error: {
            code: "AUTHENTICATION_FAILED",
            message: "Authentication credentials are invalid.",
            fields: {},
          },
          request_id: "req-revoked",
        });
      }
      return json(401, {
        success: false,
        error: {
          code: "ACCESS_TOKEN_EXPIRED",
          message: "The access token has expired.",
          fields: {},
        },
        request_id: "req-expired",
      });
    });
    const client = createApiClient({
      baseUrl: "https://api.example.com",
      fetchImpl,
      getAccessToken: () => "access-1",
      setAccessToken: () => undefined,
      getRefreshToken: async () => "refresh-1",
      setRefreshToken: async () => undefined,
      onSessionRevoked: revoked,
    });
    await expect(client.request("/api/v1/member/me/")).rejects.toBeInstanceOf(
      ApiError,
    );
    expect(revoked).toHaveBeenCalledTimes(1);
  });

  it("sends a booking idempotency key and omits it from check-in scan", async () => {
    const calls: Array<{
      url: string;
      headers: Record<string, string>;
      body?: string;
    }> = [];
    const fetchImpl: FetchLike = jest.fn(async (url, init) => {
      calls.push({ url, headers: init.headers, body: init.body });
      expect(init.headers[BFF_SECRET_HEADER]).toBeUndefined();
      if (url.includes("class-bookings")) {
        return json(201, {
          success: true,
          message: "",
          data: {
            id: "booking-1",
            kind: "CLASS",
            status: "BOOKED",
            starts_at: "2026-10-21T09:00:00+07:00",
            ends_at: "2026-10-21T10:00:00+07:00",
            trainer_display_name: "Ada",
            class_name: "Lift",
            location: null,
            member_service_package_id: "package-1",
            unmetered: false,
          },
        });
      }
      return json(201, {
        success: true,
        message: "",
        data: {
          id: "visit-1",
          member: {
            id: "member-1",
            member_number: "M1",
            first_name: "Ana",
            last_name: "Lee",
          },
          membership_id: "membership-1",
          check_in_at: "2026-10-20T08:00:00+07:00",
          check_out_at: null,
          method: "QR",
          status: "OPEN",
          note: "",
          eligibility: {
            eligible: true,
            backdated: false,
            membership_id: "membership-1",
            membership_status: "ACTIVE",
            plan_code: "M",
            plan_name: "Monthly",
            start_date: "2026-10-01",
            expiry_date: "2026-11-01",
          },
        },
      });
    });
    const api = createMemberApi(
      createApiClient({
        baseUrl: "http://10.0.2.2:8000",
        fetchImpl,
        getAccessToken: () => "access",
        setAccessToken: () => undefined,
        getRefreshToken: async () => "refresh",
        setRefreshToken: async () => undefined,
        onSessionRevoked: async () => undefined,
      }),
    );
    await api.bookClass(
      { occurrence_id: "occurrence-1", member_service_package_id: "package-1" },
      "booking-key-123456",
    );
    await api.scan("gms-checkin:v1:secret-token");
    const booking = calls[0];
    const scan = calls[1];
    expect(booking?.headers["Idempotency-Key"]).toBe("booking-key-123456");
    expect(scan?.headers["Idempotency-Key"]).toBeUndefined();
    expect(scan?.body).toBe(
      JSON.stringify({ payload: "gms-checkin:v1:secret-token" }),
    );
    expect(JSON.stringify(calls)).not.toContain(BFF_SECRET_HEADER);
  });

  it("loads the signed-in gym logo and treats a missing logo as absent", async () => {
    let logos = 0;
    const fetchImpl: FetchLike = jest.fn(async (url) => {
      if (url.endsWith("/api/v1/member/gym/logo/")) {
        logos += 1;
        if (logos > 1) {
          return json(404, {
            success: false,
            error: { code: "NOT_FOUND", message: "Not found.", fields: {} },
            request_id: "req-missing",
          });
        }
        return {
          status: 200,
          headers: {
            get: (name: string) =>
              name.toLowerCase() === "content-type" ? "image/png" : null,
          },
          text: async (): Promise<string> => "",
          bytes: async () => Uint8Array.from([1, 2, 3]),
        };
      }
      return json(404, {
        success: false,
        error: { code: "NOT_FOUND", message: "Not found.", fields: {} },
        request_id: "req-missing",
      });
    });
    const api = createMemberApi(
      createApiClient({
        baseUrl: "https://api.example.com",
        fetchImpl,
        getAccessToken: () => "access",
        setAccessToken: () => undefined,
        getRefreshToken: async () => "refresh",
        setRefreshToken: async () => undefined,
        onSessionRevoked: async () => undefined,
      }),
    );
    expect(await api.gymLogo()).toEqual({
      contentType: "image/png",
      bytes: Uint8Array.from([1, 2, 3]),
    });
    expect(await api.gymLogo()).toBeNull();
    const headers = (fetchImpl as jest.Mock).mock.calls[0][1].headers;
    expect(headers.Authorization).toBe("Bearer access");
    expect(headers.Accept).toBe("image/jpeg, image/png, image/webp");
  });

  it("sends the member language on each request", async () => {
    const fetchImpl: FetchLike = jest.fn(async () =>
      json(200, { success: true, data: { ok: true }, message: "" }),
    );
    const client = createApiClient({
      baseUrl: "https://api.example.com",
      fetchImpl,
      getAccessToken: () => "access",
      setAccessToken: () => undefined,
      getRefreshToken: async () => "refresh",
      setRefreshToken: async () => undefined,
      onSessionRevoked: async () => undefined,
      getAcceptLanguage: () => "my",
    });
    await client.request("/api/v1/member/me/");
    const init = (fetchImpl as jest.Mock).mock.calls[0][1];
    expect(init.headers["Accept-Language"]).toBe("my");
  });
});
