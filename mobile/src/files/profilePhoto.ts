import type { MemberApi } from "../api/memberApi";
import { ApiError } from "../api/errors";
import {
  decodeBase64,
  safePhotoName,
  sha256Hex,
  validatePhoto,
  type PhotoContentType,
} from "./checksum";

export type SelectedPhoto = {
  uri: string;
  fileName: string;
  contentType: string;
  base64: string;
};

export type PhotoUploadResult = {
  fileId: string;
  scanStatus: string;
  attachable: boolean;
};

export async function uploadProfilePhoto(
  api: Pick<MemberApi, "createPhotoIntent" | "completePhoto">,
  photo: SelectedPhoto,
  postForm: (target: {
    url: string;
    fileField: string;
    fields: Record<string, string>;
    uri: string;
    name: string;
    contentType: string;
  }) => Promise<void>,
): Promise<PhotoUploadResult> {
  const bytes = decodeBase64(photo.base64);
  const contentType = validatePhoto({
    contentType: photo.contentType,
    byteSize: bytes.byteLength,
  });
  const digest = sha256Hex(bytes);
  const intent = await api.createPhotoIntent({
    original_name: safePhotoName(photo.fileName, contentType),
    content_type: contentType,
    byte_size: bytes.byteLength,
    sha256: digest,
  });
  await postForm({
    url: intent.upload.url,
    fileField: intent.upload.file_field,
    fields: intent.upload.fields,
    uri: photo.uri,
    name: safePhotoName(photo.fileName, contentType),
    contentType,
  });
  try {
    const completed = await api.completePhoto(intent.file_id, {
      sha256: digest,
      byte_size: bytes.byteLength,
    });
    return {
      fileId: completed.file_id,
      scanStatus: completed.scan_status,
      attachable: completed.attachable,
    };
  } catch (error) {
    if (error instanceof ApiError && error.code === "FILE_SCAN_IN_PROGRESS") {
      return {
        fileId: intent.file_id,
        scanStatus: "SCANNING",
        attachable: false,
      };
    }
    throw error;
  }
}

export async function postPresignedPhoto(
  target: {
    url: string;
    fileField: string;
    fields: Record<string, string>;
    uri: string;
    name: string;
    contentType: PhotoContentType | string;
  },
  fetchImpl: typeof fetch = fetch,
): Promise<void> {
  const form = new FormData();
  for (const [key, value] of Object.entries(target.fields)) {
    form.append(key, value);
  }
  form.append(target.fileField, {
    uri: target.uri,
    name: target.name,
    type: target.contentType,
  } as unknown as Blob);
  let response: Response;
  try {
    response = await fetchImpl(target.url, { method: "POST", body: form });
  } catch {
    throw new Error("The photo could not be uploaded.");
  }
  if (!response.ok) {
    throw new Error("The photo upload was rejected.");
  }
}
