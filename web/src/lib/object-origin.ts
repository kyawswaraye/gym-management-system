const LOCAL_HTTP_HOSTS = new Set(["localhost", "127.0.0.1", "::1"]);

function isExplicitLocalHost(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  return LOCAL_HTTP_HOSTS.has(host) || host.endsWith(".localhost");
}

function allowedOrigins(
  nodeEnv: string | undefined,
  configuredOrigins: string | undefined,
  allowLocalHttp: string | undefined,
): Set<string> {
  // next build inlines NODE_ENV=production. E2E sets the opt-in so that
  // production bundle can still post to an explicit local HTTP object proxy.
  const localHttpAllowed = nodeEnv !== "production" || allowLocalHttp === "1";
  const allowed = new Set<string>();
  for (const entry of (configuredOrigins ?? "").split(",")) {
    const value = entry.trim();
    if (!value) continue;
    let url: URL;
    try {
      url = new URL(value);
    } catch {
      continue;
    }
    if (url.username || url.password || url.origin !== value) continue;
    if (url.protocol === "https:") {
      allowed.add(url.origin);
      continue;
    }
    if (
      localHttpAllowed &&
      url.protocol === "http:" &&
      isExplicitLocalHost(url.hostname)
    ) {
      allowed.add(url.origin);
    }
  }
  return allowed;
}

export function isConfiguredObjectUrl(
  value: string,
  nodeEnv: string | undefined,
  configuredOrigins: string | undefined,
  allowLocalHttp?: string,
): boolean {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return false;
  }
  if (url.username || url.password) return false;
  if (url.protocol !== "https:" && url.protocol !== "http:") return false;
  return allowedOrigins(nodeEnv, configuredOrigins, allowLocalHttp).has(
    url.origin,
  );
}

export function isAllowedObjectUrl(value: string): boolean {
  return isConfiguredObjectUrl(
    value,
    process.env.NODE_ENV,
    process.env.NEXT_PUBLIC_OBJECT_ORIGINS,
    process.env.NEXT_PUBLIC_ALLOW_LOCAL_OBJECT_HTTP,
  );
}
