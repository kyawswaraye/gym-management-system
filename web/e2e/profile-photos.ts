import { createHash } from "node:crypto";

export type ProfilePhotoFixture = {
  contentType: "image/jpeg" | "image/png" | "image/webp";
  fileName: string;
  sha256: string;
  bytes: Buffer;
};

const FIXTURES = [
  {
    contentType: "image/png",
    fileName: "e2e-profile.png",
    sha256: "4ea7499d8cfc60b862a856841cc11ede7dd77c360cf80d703c5bfbd6c1597979",
    encoded:
      "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFElEQVR4nGNUSFjAgA0wYRUdtBIA5UgBMGHyow0AAAAASUVORK5CYII=",
  },
  {
    contentType: "image/jpeg",
    fileName: "e2e-profile.jpg",
    sha256: "e06a0e85ef415dfba65f77daf18bb1b39473a9ba23e8ce84fdf919a063a006a0",
    encoded:
      "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYIDAoMDAsKCwsNDhIQDQ4RDgsLEBYQERMUFRUVDA8XGBYUGBIUFRT/2wBDAQMEBAUEBQkFBQkUDQsNFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBT/wAARCAAIAAgDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDwSiiivz8/rs//2Q==",
  },
  {
    contentType: "image/webp",
    fileName: "e2e-profile.webp",
    sha256: "64f905f5896dda3eeaf1495bbc6b20caf651b7eadb95ea2b93c9ab4b2fda62cb",
    encoded: "UklGRh4AAABXRUJQVlA4TBEAAAAvB8ABAAdQxqLUqP+BiOh/AAA=",
  },
] as const;

function photo(fixture: (typeof FIXTURES)[number]): ProfilePhotoFixture {
  const bytes = Buffer.from(fixture.encoded, "base64");
  return {
    contentType: fixture.contentType,
    fileName: fixture.fileName,
    sha256: fixture.sha256,
    bytes,
  };
}

export const profilePhotos = {
  png: photo(FIXTURES[0]),
  jpeg: photo(FIXTURES[1]),
  webp: photo(FIXTURES[2]),
} as const;

function hasMagic(
  bytes: Buffer,
  contentType: ProfilePhotoFixture["contentType"],
): boolean {
  if (contentType === "image/png") {
    return bytes
      .subarray(0, 8)
      .equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]));
  }
  if (contentType === "image/jpeg") {
    return bytes.subarray(0, 3).equals(Buffer.from([0xff, 0xd8, 0xff]));
  }
  return (
    bytes.subarray(0, 4).equals(Buffer.from("RIFF")) &&
    bytes.subarray(8, 12).equals(Buffer.from("WEBP"))
  );
}

export function assertProfilePhotoFixtures(): void {
  for (const fixture of Object.values(profilePhotos)) {
    const digest = createHash("sha256").update(fixture.bytes).digest("hex");
    if (
      digest !== fixture.sha256 ||
      !hasMagic(fixture.bytes, fixture.contentType)
    ) {
      throw new Error(`${fixture.fileName} is not the committed image.`);
    }
  }
}
