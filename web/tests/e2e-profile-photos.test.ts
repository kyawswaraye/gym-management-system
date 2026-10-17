import { createHash } from "node:crypto";
import { describe, expect, test } from "vitest";

import {
  assertProfilePhotoFixtures,
  profilePhotos,
} from "../e2e/profile-photos";

describe("phase 2 e2e profile photos", () => {
  test("are real jpeg, png, and webp bytes", () => {
    assertProfilePhotoFixtures();
    for (const fixture of Object.values(profilePhotos)) {
      expect(createHash("sha256").update(fixture.bytes).digest("hex")).toBe(
        fixture.sha256,
      );
      expect(fixture.bytes.byteLength).toBeGreaterThan(8);
    }
  });
});
