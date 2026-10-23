"use client";

import { useEffect, useRef, useState } from "react";

import {
  deleteFileAsset,
  getFileDownloadUrl,
  uploadProfilePhoto,
} from "@/features/operations/client";

const ACCEPTED_PHOTO = "image/jpeg,image/png,image/webp";
const CLEANUP_WARNING =
  "The previous photo could not be removed and will be deleted later.";

type PhotoRecovery = {
  fileId: string;
  canAttach: boolean;
};

function errorText(caught: unknown, fallback: string): string {
  return caught instanceof Error && caught.message ? caught.message : fallback;
}

function recoveryFrom(caught: unknown): PhotoRecovery | null {
  if (typeof caught !== "object" || caught === null) return null;
  if (!("recoverableFileId" in caught)) return null;
  const fileId = caught.recoverableFileId;
  if (typeof fileId !== "string" || fileId.length === 0) return null;
  const canAttach = "attachReady" in caught && caught.attachReady === true;
  return { fileId, canAttach };
}

function safeMemberName(memberName: string): string {
  const name = memberName.replace(/[\u0000-\u001f\u007f]/g, "").trim();
  return name || "this member";
}

export function MemberPhoto({
  memberId,
  assetId,
  memberName,
  canUpload,
  canView,
  canDelete,
  onAttach,
  onRemove,
}: {
  memberId: string;
  assetId: string | null;
  memberName: string;
  canUpload: boolean;
  canView: boolean;
  canDelete: boolean;
  onAttach: (fileId: string) => Promise<void>;
  onRemove: () => Promise<void>;
}) {
  const viewKey = canView && assetId ? `${memberId}:${assetId}` : "";
  const [seenMemberId, setSeenMemberId] = useState(memberId);
  const [seenViewKey, setSeenViewKey] = useState(viewKey);
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  const [photoLoading, setPhotoLoading] = useState(Boolean(viewKey));
  const [photoError, setPhotoError] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [recovery, setRecovery] = useState<PhotoRecovery | null>(null);
  const [cleanupWarning, setCleanupWarning] = useState("");
  const actionLock = useRef(false);
  const actionGeneration = useRef(0);
  const photoRequest = useRef(0);
  const retiredAssetIds = useRef(new Set<string>());

  if (seenMemberId !== memberId) {
    setSeenMemberId(memberId);
    setBusy(false);
    setRecovery(null);
    setPhotoError("");
    setStatus("");
    setCleanupWarning("");
  }

  useEffect(() => {
    return () => {
      actionLock.current = false;
      actionGeneration.current += 1;
    };
  }, [memberId]);
  if (seenViewKey !== viewKey) {
    setSeenViewKey(viewKey);
    setPhotoUrl(null);
    setPhotoLoading(Boolean(viewKey));
    setPhotoError("");
  }

  useEffect(() => {
    if (!canView || !assetId) return undefined;
    const request = ++photoRequest.current;
    void getFileDownloadUrl(assetId)
      .then((photo) => {
        if (request !== photoRequest.current) return;
        setPhotoUrl(photo.url);
        setPhotoLoading(false);
      })
      .catch((caught: unknown) => {
        if (request !== photoRequest.current) return;
        setPhotoUrl(null);
        setPhotoLoading(false);
        setPhotoError(errorText(caught, "Unable to load the profile photo."));
      });
    return () => {
      photoRequest.current += 1;
    };
  }, [assetId, canView, memberId]);

  if (!canUpload && !canView && !canDelete) return null;

  async function retirePrevious(previousId: string | null, nextId: string) {
    if (!canDelete || !previousId || previousId === nextId) return "";
    if (retiredAssetIds.current.has(previousId)) return "";
    retiredAssetIds.current.add(previousId);
    try {
      await deleteFileAsset(previousId);
    } catch {
      return CLEANUP_WARNING;
    }
    return "";
  }

  async function attachCompleted(
    fileId: string,
    request: number,
    previousAssetId: string | null,
  ) {
    try {
      await onAttach(fileId);
      const warning = await retirePrevious(previousAssetId, fileId);
      if (request !== actionGeneration.current) return;
      setRecovery(null);
      setPhotoError("");
      setCleanupWarning(warning);
      setStatus("Profile photo saved.");
    } catch (caught) {
      if (request !== actionGeneration.current) return;
      setCleanupWarning("");
      setRecovery({ fileId, canAttach: true });
      setStatus("");
      setPhotoError(errorText(caught, "Unable to attach the profile photo."));
    }
  }

  async function uploadSelected(file: File) {
    if (actionLock.current || recovery) return;
    const previousAssetId = assetId;
    const request = ++actionGeneration.current;
    actionLock.current = true;
    setBusy(true);
    setPhotoError("");
    setCleanupWarning("");
    setStatus("Uploading profile photo.");
    try {
      const uploaded = await uploadProfilePhoto(memberId, file);
      if (request !== actionGeneration.current) return;
      setStatus("Saving profile photo.");
      await attachCompleted(uploaded.fileId, request, previousAssetId);
    } catch (caught) {
      if (request !== actionGeneration.current) return;
      const pending = recoveryFrom(caught);
      setRecovery(pending);
      setStatus("");
      setPhotoError(errorText(caught, "Unable to upload the profile photo."));
    } finally {
      if (request === actionGeneration.current) {
        actionLock.current = false;
        setBusy(false);
      }
    }
  }

  async function retryAttach() {
    if (!recovery?.canAttach || actionLock.current) return;
    const previousAssetId = assetId;
    const request = ++actionGeneration.current;
    const fileId = recovery.fileId;
    actionLock.current = true;
    setBusy(true);
    setPhotoError("");
    setCleanupWarning("");
    setStatus("Saving profile photo.");
    try {
      await attachCompleted(fileId, request, previousAssetId);
    } finally {
      if (request === actionGeneration.current) {
        actionLock.current = false;
        setBusy(false);
      }
    }
  }

  async function deleteRecoverable() {
    if (!recovery || !canDelete || actionLock.current) return;
    const request = ++actionGeneration.current;
    const fileId = recovery.fileId;
    actionLock.current = true;
    setBusy(true);
    setPhotoError("");
    setStatus("Removing profile photo.");
    try {
      await deleteFileAsset(fileId);
      if (request !== actionGeneration.current) return;
      setRecovery(null);
      setStatus("Unattached profile photo deleted.");
    } catch (caught) {
      if (request !== actionGeneration.current) return;
      setStatus("");
      setPhotoError(
        errorText(caught, "Unable to delete the unattached profile photo."),
      );
    } finally {
      if (request === actionGeneration.current) {
        actionLock.current = false;
        setBusy(false);
      }
    }
  }

  async function removeCurrent() {
    if (!assetId || !canDelete || actionLock.current || recovery) return;
    const request = ++actionGeneration.current;
    actionLock.current = true;
    setBusy(true);
    setPhotoError("");
    setStatus("Removing profile photo.");
    try {
      await deleteFileAsset(assetId);
      if (request !== actionGeneration.current) return;
      await onRemove();
      if (request !== actionGeneration.current) return;
      setPhotoUrl(null);
      setStatus("Profile photo removed.");
    } catch (caught) {
      if (request !== actionGeneration.current) return;
      setStatus("");
      setPhotoError(errorText(caught, "Unable to remove the profile photo."));
    } finally {
      if (request === actionGeneration.current) {
        actionLock.current = false;
        setBusy(false);
      }
    }
  }

  const showUpload = canUpload && !recovery;
  const uploadLabel = assetId
    ? "Replace profile photo"
    : "Upload profile photo";

  return (
    <section
      aria-busy={busy || photoLoading}
      aria-labelledby="profile-photo-title"
    >
      <h2 className="font-bold" id="profile-photo-title">
        Profile photo
      </h2>
      {photoLoading ? (
        <p className="muted" role="status">
          Loading profile photo.
        </p>
      ) : null}
      {status ? (
        <p className="muted" role="status">
          {status}
        </p>
      ) : null}
      {cleanupWarning ? (
        <p className="muted" role="status">
          {cleanupWarning}
        </p>
      ) : null}
      {photoError ? (
        <p className="error" role="alert">
          {photoError}
        </p>
      ) : null}
      {canView && photoUrl ? (
        <img
          alt={`Profile photo of ${safeMemberName(memberName)}`}
          className="mt-3 h-32 w-32 rounded object-cover"
          onError={() => {
            setPhotoUrl(null);
            setPhotoError("Unable to display the profile photo.");
          }}
          src={photoUrl}
        />
      ) : null}
      {canView && !assetId && !photoLoading ? (
        <p className="muted mt-3">No profile photo.</p>
      ) : null}
      {photoError && canView && assetId && !photoUrl && !photoLoading ? (
        <button
          className="button mt-3"
          disabled={busy}
          onClick={() => {
            setPhotoError("");
            setPhotoUrl(null);
            setPhotoLoading(true);
            const request = ++photoRequest.current;
            void getFileDownloadUrl(assetId)
              .then((photo) => {
                if (request !== photoRequest.current) return;
                setPhotoUrl(photo.url);
                setPhotoLoading(false);
              })
              .catch((caught: unknown) => {
                if (request !== photoRequest.current) return;
                setPhotoLoading(false);
                setPhotoError(
                  errorText(caught, "Unable to load the profile photo."),
                );
              });
          }}
          type="button"
        >
          Retry loading profile photo
        </button>
      ) : null}
      <div className="mt-3 grid gap-3">
        {showUpload ? (
          <div className="grid gap-1">
            <label className="field">
              {uploadLabel}
              <input
                accept={ACCEPTED_PHOTO}
                aria-describedby="profile-photo-hint"
                className="input"
                disabled={busy}
                onChange={(event) => {
                  const selected = event.currentTarget.files;
                  const chosen = selected?.length === 1 ? selected[0] : null;
                  const tooMany = Boolean(selected && selected.length > 1);
                  event.currentTarget.value = "";
                  if (tooMany) {
                    setPhotoError("Choose one profile photo.");
                    return;
                  }
                  if (chosen) void uploadSelected(chosen);
                }}
                type="file"
              />
            </label>
            <span className="muted" id="profile-photo-hint">
              JPEG, PNG, or WebP up to 5 MB.
            </span>
          </div>
        ) : null}
        {canDelete && assetId && !recovery ? (
          <button
            className="button button-delete"
            disabled={busy}
            onClick={() => void removeCurrent()}
            type="button"
          >
            Remove profile photo
          </button>
        ) : null}
        {recovery ? (
          <div className="grid gap-3">
            <p>
              {recovery.canAttach
                ? "The profile photo was uploaded and still needs to be attached."
                : "The profile photo upload did not finish."}
            </p>
            {recovery.canAttach ? (
              <button
                className="button"
                disabled={busy}
                onClick={() => void retryAttach()}
                type="button"
              >
                Retry attaching profile photo
              </button>
            ) : null}
            {canDelete ? (
              <button
                className="button button-delete"
                disabled={busy}
                onClick={() => void deleteRecoverable()}
                type="button"
              >
                Delete unattached profile photo
              </button>
            ) : (
              <p className="muted">
                You do not have permission to delete this unfinished photo.
                Another upload stays paused.
              </p>
            )}
          </div>
        ) : null}
      </div>
    </section>
  );
}
