import { ApiError, isRevokedSession, NetworkError } from "../api/errors";
import type { ApiClient } from "../api/client";
import type { SecureStore } from "./secureStore";
import type { TokenMemory } from "./memory";

export type SessionStatus = "loading" | "anonymous" | "signed-in" | "offline";

export type Session = {
  bootstrap(): Promise<SessionStatus>;
  login(email: string, password: string): Promise<void>;
  logout(): Promise<{ serverConfirmed: boolean }>;
  wipe(): Promise<void>;
};

export function createSession(deps: {
  api: ApiClient;
  store: SecureStore;
  memory: TokenMemory;
  clearCache: () => void;
}): Session {
  let wiping: Promise<void> | null = null;

  async function wipe(): Promise<void> {
    if (!wiping) {
      wiping = (async () => {
        deps.memory.setAccessToken(null);
        await deps.store.resetAll();
        deps.clearCache();
      })().finally(() => {
        wiping = null;
      });
    }
    return wiping;
  }

  async function logout(): Promise<{ serverConfirmed: boolean }> {
    const refreshToken = await deps.store.getRefreshToken();
    const deviceId = await deps.store.getDeviceId();
    if (!deps.memory.getAccessToken() && refreshToken) {
      try {
        await deps.api.refresh();
      } catch (error) {
        if (isRevokedSession(error) || error instanceof NetworkError) {
          await wipe();
          return { serverConfirmed: isRevokedSession(error) };
        }
      }
    }
    if (deviceId && deps.memory.getAccessToken()) {
      try {
        await deps.api.request(`/api/v1/member/devices/${deviceId}/`, {
          method: "DELETE",
        });
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 404) {
          if (isRevokedSession(error)) {
            await wipe();
            return { serverConfirmed: true };
          }
        }
      }
    }
    let serverConfirmed = false;
    if (refreshToken) {
      try {
        await deps.api.logout(refreshToken);
        serverConfirmed = true;
      } catch (error) {
        if (
          isRevokedSession(error) ||
          (error instanceof ApiError && error.status === 401)
        ) {
          serverConfirmed = true;
        } else if (!(error instanceof NetworkError)) {
          throw error;
        }
      }
    }
    await wipe();
    return { serverConfirmed };
  }

  return {
    wipe,
    async bootstrap() {
      const refreshToken = await deps.store.getRefreshToken();
      if (!refreshToken) {
        return "anonymous";
      }
      try {
        await deps.api.refresh();
        return "signed-in";
      } catch (error) {
        if (isRevokedSession(error)) {
          return "anonymous";
        }
        if (error instanceof NetworkError || error instanceof ApiError) {
          return "offline";
        }
        return "offline";
      }
    },
    async login(email, password) {
      const tokens = await deps.api.login({ email, password });
      try {
        await deps.store.setRefreshToken(tokens.refresh_token);
      } catch (error) {
        deps.memory.setAccessToken(null);
        throw error;
      }
      deps.memory.setAccessToken(tokens.access_token);
    },
    logout,
  };
}
