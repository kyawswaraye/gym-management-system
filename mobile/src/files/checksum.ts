import { sha256 } from "js-sha256";

export const MAX_PHOTO_BYTES = 5_242_880;

export const ALLOWED_PHOTO_TYPES = [
  "image/jpeg",
  "image/png",
  "image/webp",
] as const;

export type PhotoContentType = (typeof ALLOWED_PHOTO_TYPES)[number];

export function isPhotoContentType(value: string): value is PhotoContentType {
  return (ALLOWED_PHOTO_TYPES as readonly string[]).includes(value);
}

export function validatePhoto(input: {
  contentType: string;
  byteSize: number;
}): PhotoContentType {
  if (!isPhotoContentType(input.contentType)) {
    throw new Error("Choose a JPEG, PNG, or WebP photo.");
  }
  if (!Number.isInteger(input.byteSize) || input.byteSize < 1) {
    throw new Error("The photo is empty.");
  }
  if (input.byteSize > MAX_PHOTO_BYTES) {
    throw new Error("The photo must be 5 MB or smaller.");
  }
  return input.contentType;
}

export function decodeBase64(value: string): Uint8Array {
  const normalized = value.replace(/\s/g, "");
  if (normalized.length % 4 !== 0 || /[^A-Za-z0-9+/=]/.test(normalized)) {
    throw new Error("The photo could not be read.");
  }
  const padding = normalized.endsWith("==")
    ? 2
    : normalized.endsWith("=")
      ? 1
      : 0;
  const byteLength = (normalized.length / 4) * 3 - padding;
  const bytes = new Uint8Array(byteLength);
  const alphabet =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  let offset = 0;
  for (let index = 0; index < normalized.length; index += 4) {
    const a = alphabet.indexOf(normalized[index] ?? "");
    const b = alphabet.indexOf(normalized[index + 1] ?? "");
    const c =
      normalized[index + 2] === "="
        ? 0
        : alphabet.indexOf(normalized[index + 2] ?? "");
    const d =
      normalized[index + 3] === "="
        ? 0
        : alphabet.indexOf(normalized[index + 3] ?? "");
    if (a < 0 || b < 0 || c < 0 || d < 0) {
      throw new Error("The photo could not be read.");
    }
    const chunk = (a << 18) | (b << 12) | (c << 6) | d;
    bytes[offset] = (chunk >> 16) & 255;
    offset += 1;
    if (normalized[index + 2] !== "=") {
      bytes[offset] = (chunk >> 8) & 255;
      offset += 1;
    }
    if (normalized[index + 3] !== "=") {
      bytes[offset] = chunk & 255;
      offset += 1;
    }
  }
  return bytes;
}

export function sha256Hex(bytes: Uint8Array): string {
  return sha256(bytes);
}

export function safePhotoName(
  name: string,
  contentType: PhotoContentType,
): string {
  const extension =
    contentType === "image/png"
      ? "png"
      : contentType === "image/webp"
        ? "webp"
        : "jpg";
  const base = name.replace(/[^A-Za-z0-9._-]/g, "").slice(0, 40);
  return base.includes(".") ? base : `profile.${extension}`;
}
