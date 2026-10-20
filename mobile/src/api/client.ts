import type { components } from "../types/api.generated";
import {
  ApiError,
  NetworkError,
  isAccessTokenExpired,
  isRevokedSession,
  parseErrorBody,
  parseSuccessBody,
  type SuccessEnvelope,
} from "./errors";

export const BFF_SECRET_HEADER = "X-Gym-BFF-Secret";

type TokenData = components["schemas"]["MemberTokenData"];

export type FetchResponse = {
  status: number;
  headers: { get(name: string): string | null };
  text(): Promise<string>;
};

export type FetchLike = (
  url: string,
  init: { method: string; headers: Record<string, string>; body?: string },
) => Promise<FetchResponse>;

export type ApiClient = {
  request<T>(
    path: string,
    init?: {
      method?: string;
      body?: unknown;
      query?: Record<string, string | number | undefined>;
      auth?: boolean;
      idempotencyKey?: string;
    },
  ): Promise<SuccessEnvelope<T> | undefined>;
  login(
    body: components["schemas"]["MemberLoginInputRequest"],
  ): Promise<TokenData>;
  refresh(): Promise<TokenData>;
  logout(refreshToken: string): Promise<void>;
};

export type ApiClientOptions = {
  baseUrl: string;
  fetchImpl?: FetchLike;
  getAccessToken: () => string | null;
  setAccessToken: (token: string | null) => void;
  getRefreshToken: () => Promise<string | null>;
  setRefreshToken: (token: string) => Promise<void>;
  onSessionRevoked: () => Promise<void>;
};

type RawResponse = {
  status: number;
  retryAfter: string | null;
  text: string;
};

export function createApiClient(options: ApiClientOptions): ApiClient {
  const fetchImpl =
    options.fetchImpl ?? (globalThis.fetch as unknown as FetchLike);
  let refreshInFlight: Promise<TokenData> | null = null;

  function urlFor(
    path: string,
    query?: Record<string, string | number | undefined>,
  ): string {
    const url = new URL(path, `${options.baseUrl}/`);
    if (query) {
      for (const [key, value] of Object.entries(query)) {
        if (value !== undefined) {
          url.searchParams.set(key, String(value));
        }
      }
    }
    return url.toString();
  }

  async function send(
    path: string,
    init: {
      method: string;
      body?: unknown;
      query?: Record<string, string | number | undefined>;
      auth?: boolean;
      idempotencyKey?: string;
    },
  ): Promise<RawResponse> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (init.body !== undefined) {
      headers["Content-Type"] = "application/json";
    }
    if (init.auth !== false) {
      const accessToken = options.getAccessToken();
      if (accessToken) {
        headers.Authorization = `Bearer ${accessToken}`;
      }
    }
    if (init.idempotencyKey) {
      headers["Idempotency-Key"] = init.idempotencyKey;
    }
    if (
      Object.keys(headers).some(
        (key) => key.toLowerCase() === BFF_SECRET_HEADER.toLowerCase(),
      )
    ) {
      throw new Error("The member app cannot send the browser secret.");
    }
    let response: FetchResponse;
    try {
      response = await fetchImpl(urlFor(path, init.query), {
        method: init.method,
        headers,
        body: init.body === undefined ? undefined : JSON.stringify(init.body),
      });
    } catch {
      throw new NetworkError();
    }
    return {
      status: response.status,
      retryAfter: response.headers.get("Retry-After"),
      text: await response.text(),
    };
  }

  async function performRefresh(): Promise<TokenData> {
    const refreshToken = await options.getRefreshToken();
    if (!refreshToken) {
      await options.onSessionRevoked();
      throw new ApiError(
        401,
        "AUTHENTICATION_FAILED",
        "Sign in again to continue.",
      );
    }
    const response = await send("/api/v1/member-auth/refresh/", {
      method: "POST",
      auth: false,
      body: { refresh_token: refreshToken },
    });
    if (response.status === 401) {
      await options.onSessionRevoked();
      throw parseErrorBody(response.text, response.status, response.retryAfter);
    }
    if (response.status !== 200) {
      throw parseErrorBody(response.text, response.status, response.retryAfter);
    }
    const envelope = parseSuccessBody<TokenData>(
      response.text,
      response.status,
    );
    await options.setRefreshToken(envelope.data.refresh_token);
    options.setAccessToken(envelope.data.access_token);
    return envelope.data;
  }

  function refresh(): Promise<TokenData> {
    if (!refreshInFlight) {
      refreshInFlight = performRefresh().finally(() => {
        refreshInFlight = null;
      });
    }
    return refreshInFlight;
  }

  async function request<T>(
    path: string,
    init: {
      method?: string;
      body?: unknown;
      query?: Record<string, string | number | undefined>;
      auth?: boolean;
      idempotencyKey?: string;
    } = {},
    allowRetry = true,
  ): Promise<SuccessEnvelope<T> | undefined> {
    const response = await send(path, {
      method: init.method ?? "GET",
      ...init,
    });
    if (response.status === 204) {
      return undefined;
    }
    if (response.status === 401 && init.auth !== false && allowRetry) {
      const error = parseErrorBody(
        response.text,
        response.status,
        response.retryAfter,
      );
      if (isAccessTokenExpired(error)) {
        await refresh();
        return request<T>(path, init, false);
      }
      if (isRevokedSession(error)) {
        await options.onSessionRevoked();
      }
      throw error;
    }
    if (response.status < 200 || response.status >= 300) {
      throw parseErrorBody(response.text, response.status, response.retryAfter);
    }
    return parseSuccessBody<T>(response.text, response.status);
  }

  return {
    request,
    async login(body) {
      const envelope = await request<TokenData>("/api/v1/member-auth/login/", {
        method: "POST",
        auth: false,
        body: { email: body.email, password: body.password },
      });
      if (!envelope) {
        throw new ApiError(
          500,
          "INVALID_RESPONSE",
          "The server response could not be read.",
        );
      }
      return envelope.data;
    },
    refresh,
    async logout(refreshToken) {
      await request<never>("/api/v1/member-auth/logout/", {
        method: "POST",
        auth: false,
        body: { refresh_token: refreshToken },
      });
    },
  };
}
