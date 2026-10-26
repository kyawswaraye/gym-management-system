import { spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { chmodSync, existsSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const alias = "upload";

export function ensureReleaseKeystore(androidDir) {
  const propertiesPath = resolve(androidDir, "keystore.properties");
  const keystorePath = resolve(androidDir, "app", "release.keystore");
  const hasProperties = existsSync(propertiesPath);
  const hasKeystore = existsSync(keystorePath);
  if (hasProperties && hasKeystore) {
    return { propertiesPath, keystorePath, created: false };
  }
  if (hasProperties !== hasKeystore) {
    throw new Error(
      "Release signing files do not match. Keep android/keystore.properties together with android/app/release.keystore, or delete both and run this again.",
    );
  }

  const password = randomBytes(24).toString("hex");
  const result = spawnSync(
    "keytool",
    [
      "-genkeypair",
      "-storetype",
      "PKCS12",
      "-keystore",
      keystorePath,
      "-storepass",
      password,
      "-alias",
      alias,
      "-keypass",
      password,
      "-keyalg",
      "RSA",
      "-keysize",
      "2048",
      "-validity",
      "10000",
      "-dname",
      "CN=Gym Member Upload,O=Gym Platform,C=US",
    ],
    { stdio: "inherit" },
  );
  if (result.error) {
    throw new Error(
      `keytool is not available (${result.error.message}). Install JDK 17 and retry.`,
    );
  }
  if (result.status !== 0) {
    throw new Error("keytool could not create the release keystore.");
  }

  writeFileSync(
    propertiesPath,
    [
      "storeFile=release.keystore",
      `storePassword=${password}`,
      `keyAlias=${alias}`,
      `keyPassword=${password}`,
      "",
    ].join("\n"),
    { mode: 0o600 },
  );
  chmodSync(keystorePath, 0o600);
  chmodSync(propertiesPath, 0o600);
  return { propertiesPath, keystorePath, created: true };
}
