import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { ensureReleaseKeystore } from "./ensure-release-keystore.mjs";

const root = resolve(import.meta.dirname, "..");
const androidDir = resolve(root, "android");
const envLocalPath = resolve(root, "src/config/env.local.ts");
const releaseEnvPath = resolve(root, ".env.release");

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

export function parseReleaseApiBaseUrl(raw) {
  const value = raw?.trim() ?? "";
  if (!value) {
    throw new Error("Set API_BASE_URL to an https origin with no path.");
  }
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error("API_BASE_URL must be an absolute URL.");
  }
  if (url.username || url.password) {
    throw new Error("API_BASE_URL cannot contain credentials.");
  }
  if (url.pathname !== "/" || url.search || url.hash) {
    throw new Error("API_BASE_URL must be an origin without a path or query.");
  }
  if (url.protocol !== "https:") {
    throw new Error("Release builds require an HTTPS API origin.");
  }
  return url.origin;
}

function readArgument(argv) {
  const index = argv.indexOf("--api");
  if (index === -1) {
    return undefined;
  }
  const value = argv[index + 1];
  if (!value || value.startsWith("-")) {
    throw new Error("Pass the API origin after --api.");
  }
  return value;
}

function readReleaseEnvFile(path) {
  if (!existsSync(path)) {
    return undefined;
  }
  let found;
  for (const line of readFileSync(path, "utf8").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }
    const separator = trimmed.indexOf("=");
    if (separator < 1) {
      throw new Error("Each .env.release line must be KEY=VALUE.");
    }
    const key = trimmed.slice(0, separator).trim();
    const value = trimmed.slice(separator + 1).trim();
    if (key !== "API_BASE_URL") {
      throw new Error(`Unsupported .env.release key: ${key}`);
    }
    found = value;
  }
  return found;
}

function resolveRawApiBaseUrl(argv) {
  const fromArgument = readArgument(argv);
  if (fromArgument) {
    return fromArgument;
  }
  const fromEnvironment = process.env.API_BASE_URL?.trim();
  if (fromEnvironment) {
    return fromEnvironment;
  }
  return readReleaseEnvFile(releaseEnvPath);
}

function releaseEnvSource(apiBaseUrl) {
  return `/** Generated for this Android release bundle. android-release.mjs restores the previous file. */
export const localEnv = {
  API_BASE_URL: ${JSON.stringify(apiBaseUrl)},
  APP_ENV: "release",
} as const;
`;
}

function main() {
  let apiBaseUrl;
  try {
    apiBaseUrl = parseReleaseApiBaseUrl(
      resolveRawApiBaseUrl(process.argv.slice(2)),
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    fail(
      `${message}\nPass API_BASE_URL=https://api.example.com, npm run android:release -- --api https://api.example.com, or write mobile/.env.release.`,
    );
  }

  let signing;
  try {
    signing = ensureReleaseKeystore(androidDir);
  } catch (error) {
    fail(error instanceof Error ? error.message : String(error));
  }
  if (signing.created) {
    process.stdout.write(
      "Created a local upload keystore at android/app/release.keystore. Keep android/keystore.properties with it. Neither file is committed.\n",
    );
  }

  const original = readFileSync(envLocalPath, "utf8");
  writeFileSync(envLocalPath, releaseEnvSource(apiBaseUrl));
  let status = 0;
  try {
    const result = spawnSync(
      "./gradlew",
      ["assembleRelease", "-PreactNativeArchitectures=armeabi-v7a,arm64-v8a"],
      { cwd: androidDir, stdio: "inherit" },
    );
    if (result.error) {
      process.stderr.write(`${result.error.message}\n`);
      status = 1;
    } else {
      status = result.status ?? 1;
    }
  } finally {
    writeFileSync(envLocalPath, original);
  }
  if (status !== 0) {
    process.exit(status);
  }
  process.stdout.write(
    "Release APK: android/app/build/outputs/apk/release/app-release.apk\n",
  );
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  main();
}
