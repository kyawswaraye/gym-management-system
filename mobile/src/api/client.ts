import { tx } from "../i18n/translate";
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
  bytes?(): Promise<Uint8Array>;
  arrayBuffer?(): Promise<ArrayBuffer>;
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
  login(body: { idToken: string }): Promise<TokenData>;
  refresh(): Promise<TokenData>;
  logout(refreshToken: string): Promise<void>;
  requestMedia(
    path: string,
  ): Promise<{ contentType: string; bytes: Uint8Array } | null>;
};

export type ApiClientOptions = {
  baseUrl: string;
  fetchImpl?: FetchLike;
  getAccessToken: () => string | null;
  setAccessToken: (token: string | null) => void;
  getRefreshToken: () => Promise<string | null>;
  setRefreshToken: (token: string) => Promise<void>;
  onSessionRevoked: () => Promise<void>;
  getAcceptLanguage?: () => "en" | "my";
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

  async function sendRaw(
    path: string,
    init: {
      method: string;
      body?: unknown;
      query?: Record<string, string | number | undefined>;
      auth?: boolean;
      idempotencyKey?: string;
    },
  ): Promise<FetchResponse> {
    const headers: Record<string, string> = {
      Accept: "application/json",
      "Accept-Language": options.getAcceptLanguage?.() ?? "en",
    };
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
    try {
      return await fetchImpl(urlFor(path, init.query), {
        method: init.method,
        headers,
        body: init.body === undefined ? undefined : JSON.stringify(init.body),
      });
    } catch {
      throw new NetworkError();
    }
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
    const response = await sendRaw(path, init);
    return {
      status: response.status,
      retryAfter: response.headers.get("Retry-After"),
      text: await response.text(),
    };
  }

  const logoTypes = new Set(["image/jpeg", "image/png", "image/webp"]);

  async function readBytes(response: FetchResponse): Promise<Uint8Array> {
    if (response.bytes) {
      return response.bytes();
    }
    if (typeof response.arrayBuffer === "function") {
      return new Uint8Array(await response.arrayBuffer());
    }
    throw new ApiError(
      500,
      "INVALID_RESPONSE",
      tx("The server response could not be read."),
    );
  }

  async function requestMedia(
    path: string,
    allowRetry = true,
  ): Promise<{ contentType: string; bytes: Uint8Array } | null> {
    const response = await sendRaw(path, { method: "GET" });
    const contentType = (response.headers.get("content-type") ?? "")
      .split(";")[0]
      .trim()
      .toLowerCase();
    if (response.status === 401 && allowRetry) {
      const error = parseErrorBody(
        await response.text(),
        response.status,
        response.headers.get("Retry-After"),
      );
      if (isAccessTokenExpired(error)) {
        await refresh();
        return requestMedia(path, false);
      }
      if (isRevokedSession(error)) {
        await options.onSessionRevoked();
      }
      throw error;
    }
    if (response.status === 200 && logoTypes.has(contentType)) {
      const bytes = await readBytes(response);
      return bytes.byteLength > 0 ? { contentType, bytes } : null;
    }
    if (response.status === 404 || response.status === 200) {
      await response.text();
      return null;
    }
    throw parseErrorBody(
      await response.text(),
      response.status,
      response.headers.get("Retry-After"),
    );
  }

  async function performRefresh(): Promise<TokenData> {
    const refreshToken = await options.getRefreshToken();
    if (!refreshToken) {
      await options.onSessionRevoked();
      throw new ApiError(
        401,
        "AUTHENTICATION_FAILED",
        tx("Sign in again to continue."),
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
      const envelope = await request<TokenData>("/api/v1/member-auth/google/", {
        method: "POST",
        auth: false,
        body: { id_token: body.idToken },
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
    requestMedia,
    async logout(refreshToken) {
      await request<never>("/api/v1/member-auth/logout/", {
        method: "POST",
        auth: false,
        body: { refresh_token: refreshToken },
      });
    },
  };
}
