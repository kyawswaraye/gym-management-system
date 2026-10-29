"use client";

import { useTx } from "@/i18n/use-tx";
import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
} from "react";

import { Field } from "@/features/finance/ui";
import { getFileDownloadUrl } from "@/features/operations/client";

export function ReceiptEvidencePreview({
  fileId,
}: {
  fileId: string | null | undefined;
}) {
  const tx = useTx();
  const [url, setUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(fileId));
  const [failed, setFailed] = useState(false);
  const [seenFileId, setSeenFileId] = useState(fileId ?? "");
  const requestRef = useRef(0);
  const fileKey = fileId ?? "";

  if (seenFileId !== fileKey) {
    setSeenFileId(fileKey);
    setUrl(null);
    setLoading(Boolean(fileKey));
    setFailed(false);
  }

  useEffect(() => {
    if (!fileId) return undefined;
    const request = ++requestRef.current;
    void getFileDownloadUrl(fileId)
      .then((file) => {
        if (request !== requestRef.current) return;
        setUrl(file.url);
        setLoading(false);
        setFailed(false);
      })
      .catch(() => {
        if (request !== requestRef.current) return;
        setUrl(null);
        setLoading(false);
        setFailed(true);
      });
    return () => {
      requestRef.current += 1;
    };
  }, [fileId]);

  if (!fileId) return null;

  if (loading) {
    return (
      <p className="text-sm text-slate-500" role="status">
        {tx("Loading the receipt evidence.")}
      </p>
    );
  }

  if (url) {
    return (
      <img
        alt={tx("Receipt evidence")}
        className="max-h-64 w-full rounded-xl bg-slate-50 object-contain ring-1 ring-slate-200"
        onError={() => {
          setUrl(null);
          setFailed(true);
        }}
        src={url}
      />
    );
  }

  if (failed) {
    return (
      <p className="text-sm text-slate-500" role="status">
        {tx("Unable to display the receipt evidence.")}
      </p>
    );
  }

  return null;
}

export function ReceiptEvidenceField({
  attachedFileId = null,
}: {
  attachedFileId?: string | null;
}) {
  const tx = useTx();
  const [localUrl, setLocalUrl] = useState<string | null>(null);
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
      return;
    }
    const url = URL.createObjectURL(file);
    localUrlRef.current = url;
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
        <img
          alt={tx("Receipt evidence")}
          className="mt-3 max-h-64 w-full rounded-xl bg-slate-50 object-contain ring-1 ring-slate-200"
          onError={() => replaceLocal(null)}
          src={localUrl}
        />
      ) : (
        <div className="mt-3">
          <ReceiptEvidencePreview fileId={attachedFileId} />
        </div>
      )}
    </Field>
  );
}
