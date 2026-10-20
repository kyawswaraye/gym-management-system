const IDEMPOTENCY_KEY = /^[A-Za-z0-9._:-]{16,128}$/;

export function createIdempotencyKey(
  randomUUID: () => string = randomUuidV4,
): string {
  const key = randomUUID();
  if (!IDEMPOTENCY_KEY.test(key)) {
    throw new Error("The idempotency key is not valid.");
  }
  return key;
}

type CryptoSource = {
  randomUUID?: () => string;
  getRandomValues?: (bytes: Uint8Array) => Uint8Array;
};

export function randomUuidV4(): string {
  const cryptoObject = (globalThis as { crypto?: CryptoSource }).crypto;
  if (cryptoObject && typeof cryptoObject.randomUUID === "function") {
    return cryptoObject.randomUUID();
  }
  if (!cryptoObject || typeof cryptoObject.getRandomValues !== "function") {
    throw new Error("This device cannot create a random identifier.");
  }
  const bytes = new Uint8Array(16);
  cryptoObject.getRandomValues(bytes);
  const sixth = bytes[6] ?? 0;
  const eighth = bytes[8] ?? 0;
  bytes[6] = (sixth & 0x0f) | 0x40;
  bytes[8] = (eighth & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
