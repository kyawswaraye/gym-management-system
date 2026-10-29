"use client";

import { useTx } from "@/i18n/use-tx";
import { useEffect, useRef, useState, type ChangeEvent } from "react";

import { Field } from "@/features/finance/ui";
import { getFileDownloadUrl } from "@/features/operations/client";

function ReceiptLoading() {
  const tx = useTx();
  return (
    <div className="grid gap-2" role="status">
      <p className="text-sm font-medium text-slate-600">
        {tx("Loading the receipt evidence.")}
      </p>
      <div className="h-40 animate-pulse rounded-xl bg-slate-100 ring-1 ring-slate-200" />
    </div>
  );
}

export function ReceiptEvidencePreview({
  fileId,
}: {
  fileId: string | null | undefined;
}) {
  const tx = useTx();
  const [url, setUrl] = useState<string | null>(null);
  const [fetching, setFetching] = useState(Boolean(fileId));
  const [imageReady, setImageReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [seenFileId, setSeenFileId] = useState(fileId ?? "");
  const requestRef = useRef(0);
  const fileKey = fileId ?? "";

  if (seenFileId !== fileKey) {
    setSeenFileId(fileKey);
    setUrl(null);
    setFetching(Boolean(fileKey));
    setImageReady(false);
    setFailed(false);
  }

  useEffect(() => {
    if (!fileId) return undefined;
    const request = ++requestRef.current;
    void getFileDownloadUrl(fileId)
      .then((file) => {
        if (request !== requestRef.current) return;
        setUrl(file.url);
        setFetching(false);
        setImageReady(false);
        setFailed(false);
      })
      .catch(() => {
        if (request !== requestRef.current) return;
        setUrl(null);
        setFetching(false);
        setImageReady(false);
        setFailed(true);
      });
    return () => {
      requestRef.current += 1;
    };
  }, [fileId]);

  if (!fileId) return null;

  if (fetching) return <ReceiptLoading />;

  if (url) {
    return (
      <div className="grid gap-2">
        {!imageReady ? <ReceiptLoading /> : null}
        <img
          alt={tx("Receipt evidence")}
          className={
            imageReady
              ? "max-h-64 w-full rounded-xl bg-slate-50 object-contain ring-1 ring-slate-200"
              : "pointer-events-none absolute h-0 w-0 opacity-0"
          }
          onError={() => {
            setUrl(null);
            setImageReady(false);
            setFailed(true);
          }}
          onLoad={() => setImageReady(true)}
          src={url}
        />
      </div>
    );
  }

  if (failed) {
    return (
      <p className="text-sm font-medium text-slate-600" role="status">
        {tx("Unable to display the receipt evidence.")}
      </p>
    );
  }

  return null;
}

export function ReceiptEvidenceField({
  attachedFileId = null,
}: {
  attachedFileId?: string | null | undefined;
}) {
  const tx = useTx();
  const [localUrl, setLocalUrl] = useState<string | null>(null);
  const [localReady, setLocalReady] = useState(false);
  const localUrlRef = useRef<string | null>(null);

  useEffect(() => {
    return () => {
      if (localUrlRef.current) URL.revokeObjectURL(localUrlRef.current);
    };
  }, []);

  function replaceLocal(file: File | null) {
    if (localUrlRef.current) URL.revokeObjectURL(localUrlRef.current);
    if (!file) {
      localUrlRef.current = null;
      setLocalUrl(null);
      setLocalReady(false);
      return;
    }
    const url = URL.createObjectURL(file);
    localUrlRef.current = url;
    setLocalReady(false);
    setLocalUrl(url);
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null;
    replaceLocal(file && file.size > 0 ? file : null);
  }

  return (
    <Field label="Receipt evidence">
      {attachedFileId ? (
        <input
          name="receipt_file_asset_id"
          type="hidden"
          value={attachedFileId}
        />
      ) : null}
      <input
        accept="image/jpeg,image/png,image/webp"
        className="input"
        name="receipt"
        onChange={onFileChange}
        type="file"
      />
      <p className="mt-1 text-xs text-slate-500">
        {tx("JPEG, PNG, or WebP up to 5 MB.")}
      </p>
      {localUrl ? (
        <div className="mt-3 grid gap-2">
          {!localReady ? <ReceiptLoading /> : null}
          <img
            alt={tx("Receipt evidence")}
            className={
              localReady
                ? "max-h-64 w-full rounded-xl bg-slate-50 object-contain ring-1 ring-slate-200"
                : "pointer-events-none absolute h-0 w-0 opacity-0"
            }
            onError={() => replaceLocal(null)}
            onLoad={() => setLocalReady(true)}
            src={localUrl}
          />
        </div>
      ) : (
        <div className="mt-3">
          <ReceiptEvidencePreview fileId={attachedFileId} />
        </div>
      )}
    </Field>
  );
}
