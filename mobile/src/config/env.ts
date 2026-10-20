import { z } from "zod";

import { localEnv } from "./env.local";

const LOCAL_HTTP_HOSTS = new Set(["10.0.2.2", "localhost", "127.0.0.1"]);

const rawEnvSchema = z.object({
  API_BASE_URL: z.string().min(1),
  APP_ENV: z.enum(["debug", "release"]),
});

export type AppEnv = {
  apiBaseUrl: string;
  appEnv: "debug" | "release";
};

export function parseEnv(input: {
  API_BASE_URL: string;
  APP_ENV: string;
}): AppEnv {
  const raw = rawEnvSchema.parse(input);
  let url: URL;
  try {
    url = new URL(raw.API_BASE_URL);
  } catch {
    throw new Error("API_BASE_URL must be an absolute URL.");
  }
  if (url.username || url.password) {
    throw new Error("API_BASE_URL cannot contain credentials.");
  }
  if (url.pathname !== "/" || url.search || url.hash) {
    throw new Error("API_BASE_URL must be an origin without a path or query.");
  }
  if (raw.APP_ENV === "release") {
    if (url.protocol !== "https:") {
      throw new Error("Release builds require an HTTPS API origin.");
    }
  } else if (url.protocol === "http:") {
    if (!LOCAL_HTTP_HOSTS.has(url.hostname)) {
      throw new Error("Debug HTTP is limited to the local API host.");
    }
  } else if (url.protocol !== "https:") {
    throw new Error("API_BASE_URL must use HTTP or HTTPS.");
  }
  return { apiBaseUrl: url.origin, appEnv: raw.APP_ENV };
}

export function assertRuntimeEnv(env: AppEnv, isDev: boolean): void {
  if (!isDev && env.appEnv !== "release") {
    throw new Error("A release bundle requires APP_ENV=release.");
  }
  if (!isDev && !env.apiBaseUrl.startsWith("https://")) {
    throw new Error("A release bundle requires HTTPS.");
  }
}

export const env = parseEnv(localEnv);
