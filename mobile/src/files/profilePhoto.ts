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
  bytes: Uint8Array;
};

export function photoContentType(
  type: string | null | undefined,
  fileName: string | null | undefined,
): string {
  const raw = (type ?? "").split(";")[0]?.trim().toLowerCase() ?? "";
  if (raw === "image/jpg" || raw === "image/pjpeg" || raw === "image/jpeg") {
    return "image/jpeg";
  }
  if (raw === "image/png") {
    return "image/png";
  }
  if (raw === "image/webp") {
    return "image/webp";
  }
  const name = (fileName ?? "").toLowerCase();
  if (name.endsWith(".png")) {
    return "image/png";
  }
  if (name.endsWith(".webp")) {
    return "image/webp";
  }
  if (name.endsWith(".jpg") || name.endsWith(".jpeg")) {
    return "image/jpeg";
  }
  return raw;
}

export async function readUriBytes(uri: string): Promise<Uint8Array> {
  try {
    const response = await fetch(uri);
    const buffer = await response.arrayBuffer();
    if (buffer.byteLength > 0) {
      return new Uint8Array(buffer);
    }
  } catch {
    // The file URI may only be readable through XHR on this phone.
  }
  const blob = await new Promise<Blob>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.onload = () => resolve(xhr.response as Blob);
    xhr.onerror = () => reject(new Error("The photo could not be opened."));
    xhr.responseType = "blob";
    xhr.open("GET", uri);
    xhr.send();
  });
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(new Error("The photo could not be opened."));
    reader.readAsDataURL(blob);
  });
  const encoded = dataUrl.slice(dataUrl.indexOf(",") + 1);
  return decodeBase64(encoded);
}

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
  const bytes = photo.bytes;
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
