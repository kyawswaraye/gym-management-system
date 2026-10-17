import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getCsrfToken } from "@/features/auth/client";
import {
  deleteFileAsset,
  FileTransferError,
  getFileDownloadUrl,
  uploadProfilePhoto,
} from "@/features/operations/client";

vi.mock("@/features/auth/client", () => ({
  getCsrfToken: vi.fn(async () => "csrf-token"),
  mutationHeaders: (token: string) => ({
    "Content-Type": "application/json",
    "X-CSRF-Token": token,
  }),
}));

const MEMBER_ID = "11111111-1111-4111-8111-111111111111";
const FILE_ID = "88888888-8888-4888-8888-888888888888";
const SHA256 =
  "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
const ORIGINS = "https://files.example.com,http://localhost:8088";

const signedFields = {
  key: "key-do-not-leak",
  "Content-Type": "image/png",
  policy: "policy-do-not-leak",
  "x-amz-algorithm": "AWS4-HMAC-SHA256",
  "x-amz-credential": "credential-do-not-leak",
  "x-amz-date": "20261017T100000Z",
  "x-amz-signature": "sig-do-not-leak",
  success_action_status: "204",
};

function photo(name = "ada.png", type = "image/png") {
  return new File(["abc"], name, { type });
}

function intent(overrides: Record<string, unknown> = {}) {
  return {
    file_id: FILE_ID,
    purpose: "PROFILE_PHOTO",
    owner_type: "MEMBER",
    owner_id: MEMBER_ID,
    upload: {
      method: "POST",
      url: "https://files.example.com/gym-private",
      file_field: "file",
      expires_at: "2026-10-17T10:05:00Z",
      fields: signedFields,
    },
    ...overrides,
  };
}

function completed(overrides: Record<string, unknown> = {}) {
  return {
    file_id: FILE_ID,
    purpose: "PROFILE_PHOTO",
    owner_type: "MEMBER",
    owner_id: MEMBER_ID,
    original_name: "ada.png",
    content_type: "image/png",
    byte_size: 3,
    sha256: "sha-do-not-leak",
    scan_status: "CLEAN",
    retention_status: "ACTIVE",
    attachable: true,
    ...overrides,
  };
}

describe("profile photo file client", () => {
  const logs: Array<ReturnType<typeof vi.spyOn>> = [];

  beforeEach(() => {
    vi.clearAllMocks();
    process.env.NEXT_PUBLIC_OBJECT_ORIGINS = ORIGINS;
    for (const method of ["log", "info", "debug", "warn", "error"] as const) {
      logs.push(vi.spyOn(console, method).mockImplementation(() => undefined));
    }
  });

  afterEach(() => {
    for (const log of logs) log.mockRestore();
    logs.length = 0;
    delete process.env.NEXT_PUBLIC_OBJECT_ORIGINS;
  });

  function noLeak() {
    const rendered = logs
      .flatMap((log) =>
        log.mock.calls.map((args: readonly unknown[]) =>
          args.map((item) => String(item)).join(" "),
        ),
      )
      .join("\n");
    expect(rendered).not.toContain("sig-do-not-leak");
    expect(rendered).not.toContain("key-do-not-leak");
    expect(rendered).not.toContain("policy-do-not-leak");
    expect(rendered).not.toContain("credential-do-not-leak");
    expect(rendered).not.toContain("sha-do-not-leak");
  }

  it("hashes the file, posts the intent and object, then completes", async () => {
    const file = photo();
    const objectCalls: RequestInit[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/upload-intents/")) {
        return Response.json(
          { success: true, data: intent(), message: "Created" },
          { status: 201 },
        );
      }
      if (url === "https://files.example.com/gym-private") {
        objectCalls.push(init ?? {});
        return new Response(null, { status: 204 });
      }
      if (url.endsWith("/complete/")) {
        return Response.json({
          success: true,
          data: completed(),
          message: "Success",
        });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    const result = await uploadProfilePhoto(MEMBER_ID.toUpperCase(), file);

    expect(result).toEqual({ fileId: FILE_ID });
    expect(JSON.stringify(result)).not.toContain("sha-do-not-leak");
    expect(JSON.stringify(result)).not.toContain("sig-do-not-leak");
    expect(getCsrfToken).toHaveBeenCalledTimes(2);
    const fetchMock = vi.mocked(fetch);
    const intentCall = fetchMock.mock.calls[0];
    expect(intentCall?.[0]).toBe("/api/operations/files/upload-intents/");
    expect(intentCall?.[1]?.method).toBe("POST");
    expect(intentCall?.[1]?.credentials).toBe("same-origin");
    expect(new Headers(intentCall?.[1]?.headers).get("X-CSRF-Token")).toBe(
      "csrf-token",
    );
    expect(new Headers(intentCall?.[1]?.headers).has("authorization")).toBe(
      false,
    );
    expect(JSON.parse(String(intentCall?.[1]?.body))).toEqual({
      original_name: "ada.png",
      content_type: "image/png",
      byte_size: 3,
      sha256: SHA256,
      purpose: "PROFILE_PHOTO",
      owner_type: "MEMBER",
      owner_id: MEMBER_ID,
    });
    expect(objectCalls).toHaveLength(1);
    expect(objectCalls[0]?.method).toBe("POST");
    expect(objectCalls[0]?.credentials).toBe("omit");
    expect(objectCalls[0]?.headers).toBeUndefined();
    const form = objectCalls[0]?.body as FormData;
    expect([...form.keys()]).toEqual([...Object.keys(signedFields), "file"]);
    expect(form.get("x-amz-signature")).toBe("sig-do-not-leak");
    const uploaded = form.get("file");
    expect(uploaded).toBeInstanceOf(File);
    expect((uploaded as File).name).toBe("ada.png");
    const completeCall = fetchMock.mock.calls[2];
    expect(completeCall?.[0]).toBe(
      `/api/operations/files/${FILE_ID}/complete/`,
    );
    expect(completeCall?.[1]?.method).toBe("POST");
    expect(completeCall?.[1]?.credentials).toBe("same-origin");
    expect(JSON.parse(String(completeCall?.[1]?.body))).toEqual({
      sha256: SHA256,
      byte_size: 3,
    });
    noLeak();
  });

  it("rejects type and size before calling the BFF", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    await expect(
      uploadProfilePhoto(MEMBER_ID, photo("anim.gif", "image/gif")),
    ).rejects.toThrow("Choose a JPEG, PNG, or WebP image.");
    const empty = photo();
    Object.defineProperty(empty, "size", { value: 0 });
    await expect(uploadProfilePhoto(MEMBER_ID, empty)).rejects.toThrow(
      "Choose a photo that is not empty.",
    );
    const huge = photo();
    Object.defineProperty(huge, "size", { value: 5 * 1024 * 1024 + 1 });
    await expect(uploadProfilePhoto(MEMBER_ID, huge)).rejects.toThrow(
      "Choose a photo that is 5 MB or smaller.",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects a signed target that is not a POST of file to an allowed origin", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () =>
        Response.json(
          {
            success: true,
            data: intent({
              upload: {
                method: "PUT",
                url: "https://files.example.com/gym-private",
                file_field: "upload",
                expires_at: "2026-10-17T10:05:00Z",
                fields: signedFields,
              },
            }),
            message: "Created",
          },
          { status: 201 },
        ),
      );

    const error = await uploadProfilePhoto(MEMBER_ID, photo()).then(
      () => {
        throw new Error("expected rejection");
      },
      (caught: unknown) => caught,
    );
    expect(error).toBeInstanceOf(FileTransferError);
    expect(error).toMatchObject({ recoverableFileId: FILE_ID });
    expect((error as Error).message).not.toContain("sig-do-not-leak");
    expect((error as Error).message).not.toContain("key-do-not-leak");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    noLeak();
  });

  it("requires HTTP 204 from the object store and does not complete otherwise", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.endsWith("/upload-intents/")) {
          return Response.json(
            { success: true, data: intent(), message: "Created" },
            { status: 201 },
          );
        }
        return new Response("<Error>sig-do-not-leak</Error>", { status: 200 });
      });

    const error = await uploadProfilePhoto(MEMBER_ID, photo()).catch(
      (caught: unknown) => caught,
    );
    expect(error).toBeInstanceOf(FileTransferError);
    expect(error).toMatchObject({
      recoverableFileId: FILE_ID,
      message: "The file service did not accept the profile photo.",
    });
    expect((error as Error).message).not.toContain("sig-do-not-leak");
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      "/api/operations/files/upload-intents/",
      "https://files.example.com/gym-private",
    ]);
    noLeak();
  });

  it("rejects an unexpected download URL and returns no signed metadata", async () => {
    const download = {
      url: "https://files.example.com/gym-private/photo?X-Amz-Signature=sig-do-not-leak",
      method: "GET",
      expires_at: "2026-10-17T10:05:00Z",
      filename: "profile-photo.png",
      content_type: "image/png",
      byte_size: 3,
      sha256: "sha-do-not-leak",
    };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/download-url/")) {
        return Response.json({
          success: true,
          data: download,
          message: "Success",
        });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    const photoUrl = await getFileDownloadUrl(FILE_ID.toUpperCase());
    expect(photoUrl).toEqual({
      url: download.url,
      contentType: "image/png",
    });
    expect(photoUrl).not.toHaveProperty("sha256");
    expect(JSON.stringify(photoUrl)).not.toContain("sha-do-not-leak");
    expect(vi.mocked(fetch).mock.calls[0]?.[0]).toBe(
      `/api/operations/files/${FILE_ID}/download-url/`,
    );
    expect(vi.mocked(fetch).mock.calls[0]?.[1]?.credentials).toBe(
      "same-origin",
    );

    vi.mocked(fetch).mockResolvedValue(
      Response.json({
        success: true,
        data: {
          ...download,
          method: "POST",
          url: "https://evil.example/object?X-Amz-Signature=sig-do-not-leak",
        },
        message: "Success",
      }),
    );
    const rejected = await getFileDownloadUrl(FILE_ID).catch(
      (caught: unknown) => caught,
    );
    expect(rejected).toBeInstanceOf(Error);
    expect((rejected as Error).message).toBe(
      "Unable to load the profile photo.",
    );
    expect((rejected as Error).message).not.toContain("sig-do-not-leak");
    noLeak();
  });

  it("deletes through the BFF with an empty body", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        Response.json({ success: true, data: { file_id: FILE_ID } }),
      );

    await deleteFileAsset(FILE_ID);
    expect(getCsrfToken).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      `/api/operations/files/${FILE_ID}/`,
    );
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      method: "DELETE",
      credentials: "same-origin",
      cache: "no-store",
    });
    expect(fetchMock.mock.calls[0]?.[1]?.body).toBeUndefined();
    expect(
      new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("X-CSRF-Token"),
    ).toBe("csrf-token");
    expect(
      new Headers(fetchMock.mock.calls[0]?.[1]?.headers).has("authorization"),
    ).toBe(false);
    expect(
      new Headers(fetchMock.mock.calls[0]?.[1]?.headers).has("content-type"),
    ).toBe(false);
  });

  it("allows an explicit localhost object URL in test and rejects other hosts", async () => {
    const urls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      urls.push(url);
      if (url.endsWith("/upload-intents/")) {
        return Response.json(
          {
            success: true,
            data: intent({
              upload: {
                method: "POST",
                url: "http://localhost:8088/gym-private",
                file_field: "file",
                expires_at: "2026-10-17T10:05:00Z",
                fields: signedFields,
              },
            }),
            message: "Created",
          },
          { status: 201 },
        );
      }
      if (url === "http://localhost:8088/gym-private") {
        return new Response(null, { status: 204 });
      }
      if (url.endsWith("/complete/")) {
        return Response.json({
          success: true,
          data: completed(),
          message: "Success",
        });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    await expect(uploadProfilePhoto(MEMBER_ID, photo())).resolves.toEqual({
      fileId: FILE_ID,
    });
    expect(urls).toContain("http://localhost:8088/gym-private");

    vi.mocked(fetch).mockImplementation(async () =>
      Response.json(
        {
          success: true,
          data: intent({
            upload: {
              method: "POST",
              url: "https://evil.example/gym-private",
              file_field: "file",
              expires_at: "2026-10-17T10:05:00Z",
              fields: signedFields,
            },
          }),
          message: "Created",
        },
        { status: 201 },
      ),
    );
    const error = await uploadProfilePhoto(MEMBER_ID, photo()).catch(
      (caught: unknown) => caught,
    );
    expect((error as Error).message).not.toContain("evil.example");
    expect((error as Error).message).not.toContain("sig-do-not-leak");
  });
});
