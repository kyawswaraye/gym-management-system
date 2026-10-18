import { isAllowedObjectUrl } from "@/lib/object-origin";
import {
  FileTransferError,
  OperationError,
  operationMutation,
} from "@/features/operations/client";
import type {
  CompletedFile,
  UploadIntentData,
  UploadIntentRequest,
} from "@/types/api";

const IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const MAX_BYTES = 5 * 1024 * 1024;

function failure(message: string, fileId: string | null = null): never {
  throw new FileTransferError(message, fileId);
}

async function sha256Hex(bytes: BufferSource): Promise<string> {
  if (!globalThis.crypto?.subtle) failure("Unable to upload the receipt.");
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  let hex = "";
  for (const byte of new Uint8Array(digest)) {
    hex += byte.toString(16).padStart(2, "0");
  }
  return hex;
}

export async function uploadExpenseReceipt(
  gymId: string,
  file: File,
): Promise<string> {
  if (!IMAGE_TYPES.has(file.type)) {
    throw new OperationError("Choose a JPEG, PNG, or WebP image.");
  }
  if (!Number.isInteger(file.size) || file.size < 1 || file.size > MAX_BYTES) {
    throw new OperationError("Choose an image that is 5 MB or smaller.");
  }
  const bytes = new Uint8Array(await file.arrayBuffer());
  const request: UploadIntentRequest = {
    original_name: file.name.slice(0, 255) || "expense-receipt.jpg",
    content_type: file.type,
    byte_size: file.size,
    sha256: await sha256Hex(bytes),
    purpose: "EXPENSE_RECEIPT",
    owner_type: "GYM",
    owner_id: gymId,
  };
  const intent = await operationMutation<UploadIntentData>(
    "files/upload-intents/",
    request,
  );
  const upload = intent.upload;
  if (
    intent.purpose !== "EXPENSE_RECEIPT" ||
    intent.owner_type !== "GYM" ||
    upload.method !== "POST" ||
    typeof upload.url !== "string" ||
    !isAllowedObjectUrl(upload.url)
  ) {
    failure("Unable to upload the receipt.", intent.file_id);
  }
  const form = new FormData();
  for (const [key, value] of Object.entries(upload.fields)) {
    if (key !== "file") form.append(key, value);
  }
  form.append("file", file);
  const posted = await fetch(upload.url, {
    method: "POST",
    body: form,
    credentials: "omit",
    cache: "no-store",
    redirect: "error",
    mode: "cors",
  });
  if (posted.status !== 204) {
    failure("The file service did not accept the receipt.", intent.file_id);
  }
  const completed = await operationMutation<CompletedFile>(
    `files/${intent.file_id}/complete/`,
    { sha256: request.sha256, byte_size: request.byte_size },
  );
  if (completed.attachable !== true || completed.file_id !== intent.file_id) {
    failure("Unable to upload the receipt.", intent.file_id);
  }
  return completed.file_id;
}
