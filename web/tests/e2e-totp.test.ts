import { expect, test } from "vitest";

import { totpCode } from "../e2e/totp";

const SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ";

test("totp matches the RFC 6238 SHA1 vectors", () => {
  expect(totpCode(SECRET, 59_000)).toBe("287082");
  expect(totpCode(SECRET, 1_111_111_109_000)).toBe("081804");
  expect(totpCode(SECRET, 1_234_567_890_000)).toBe("005924");
  expect(totpCode(SECRET, 2_000_000_000_000)).toBe("279037");
});
