import { createHmac } from "node:crypto";

const BASE32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

function decodeBase32(secret: string): Buffer {
  const cleaned = secret.replace(/=+$/u, "").toUpperCase();
  let bits = 0;
  let value = 0;
  const bytes: number[] = [];
  for (const char of cleaned) {
    const index = BASE32.indexOf(char);
    if (index < 0) {
      throw new Error("Invalid E2E TOTP secret.");
    }
    value = (value << 5) | index;
    bits += 5;
    if (bits >= 8) {
      bits -= 8;
      bytes.push((value >>> bits) & 0xff);
    }
  }
  return Buffer.from(bytes);
}

function digestByte(digest: Buffer, index: number): number {
  const value = digest[index];
  if (value === undefined) {
    throw new Error("Invalid TOTP digest.");
  }
  return value;
}

export function totpCode(secret: string, at = Date.now()): string {
  const counter = Math.floor(at / 1000 / 30);
  const message = Buffer.alloc(8);
  message.writeBigUInt64BE(BigInt(counter));
  const digest = createHmac("sha1", decodeBase32(secret))
    .update(message)
    .digest();
  const offset = digestByte(digest, digest.length - 1) & 0x0f;
  const binary =
    ((digestByte(digest, offset) & 0x7f) << 24) |
    ((digestByte(digest, offset + 1) & 0xff) << 16) |
    ((digestByte(digest, offset + 2) & 0xff) << 8) |
    (digestByte(digest, offset + 3) & 0xff);
  return String(binary % 1_000_000).padStart(6, "0");
}
