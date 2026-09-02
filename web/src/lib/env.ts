import "server-only";

type ServerEnv = {
  djangoApiUrl: string;
  allowedOrigins: ReadonlySet<string>;
  accessExpiredCode: string;
  secureCookies: boolean;
  bffSharedSecret: string;
};

let cached: ServerEnv | undefined;

function parseOrigin(value: string, label: string): string {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error(`${label} must be an absolute URL.`);
  }
  if (!["http:", "https:"].includes(url.protocol) || url.origin !== value) {
    throw new Error(`${label} must contain only an http(s) origin.`);
  }
  return url.origin;
}

export function getServerEnv(): ServerEnv {
  if (cached) return cached;

  const apiValue = process.env.DJANGO_API_URL;
  const originValue = process.env.APP_ORIGINS;
  const expiredCode = process.env.ACCESS_EXPIRED_CODE;
  const bffSharedSecret = process.env.BFF_SHARED_SECRET ?? "";
  if (!apiValue || !originValue || !expiredCode) {
    throw new Error(
      "Missing DJANGO_API_URL, APP_ORIGINS, or ACCESS_EXPIRED_CODE.",
    );
  }
  if (bffSharedSecret.length < 32) {
    throw new Error("BFF_SHARED_SECRET must be at least 32 characters.");
  }

  const apiUrl = new URL(apiValue);
  if (!["http:", "https:"].includes(apiUrl.protocol)) {
    throw new Error("DJANGO_API_URL must use http or https.");
  }

  const origins = originValue
    .split(",")
    .map((value, index) => parseOrigin(value.trim(), `APP_ORIGINS[${index}]`));
  if (origins.length === 0) throw new Error("APP_ORIGINS cannot be empty.");

  const allowInsecure = process.env.ALLOW_INSECURE_LOCALHOST_COOKIES === "true";
  if (
    allowInsecure &&
    origins.some((origin) => new URL(origin).hostname !== "localhost")
  ) {
    throw new Error("Insecure cookies are allowed only on localhost.");
  }

  cached = {
    djangoApiUrl: apiUrl.toString().replace(/\/$/, ""),
    allowedOrigins: new Set(origins),
    accessExpiredCode: expiredCode,
    secureCookies: !allowInsecure,
    bffSharedSecret,
  };
  return cached;
}

export function resetEnvForTests(): void {
  cached = undefined;
}
