"use client";
import { useTx } from "@/i18n/use-tx";

import { useEffect, useRef, useState } from "react";

import { getFileDownloadUrl } from "@/features/operations/client";

function safeMemberName(memberName: string): string {
  const name = memberName.replace(/[\u0000-\u001f\u007f]/g, "").trim();
  return name || "this member";
}

function EmptyProfilePhoto() {
  const tx = useTx();
  return (
    <div
      aria-label={tx("No profile photo.")}
      className="mx-auto mt-3 grid h-36 w-36 place-items-center rounded-full bg-slate-100 text-slate-300"
      role="img"
    >
      <svg
        aria-hidden="true"
        className="h-16 w-16"
        fill="currentColor"
        viewBox="0 0 24 24"
      >
        <path d="M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4Zm0 2c-3.33 0-8 1.67-8 5v1h16v-1c0-3.33-4.67-5-8-5Z" />
      </svg>
    </div>
  );
}

export function MemberPhoto({
  memberId,
  assetId,
  memberName,
  canView,
}: {
  memberId: string;
  assetId: string | null;
  memberName: string;
  canView: boolean;
}) {
  const tx = useTx();
  const viewKey = canView && assetId ? `${memberId}:${assetId}` : "";
  const [seenViewKey, setSeenViewKey] = useState(viewKey);
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  const [photoLoading, setPhotoLoading] = useState(Boolean(viewKey));
  const photoRequest = useRef(0);

  if (seenViewKey !== viewKey) {
    setSeenViewKey(viewKey);
    setPhotoUrl(null);
    setPhotoLoading(Boolean(viewKey));
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
      .catch(() => {
        if (request !== photoRequest.current) return;
        setPhotoUrl(null);
        setPhotoLoading(false);
      });
    return () => {
      photoRequest.current += 1;
    };
  }, [assetId, canView, memberId]);

  return (
    <section aria-busy={photoLoading} aria-labelledby="profile-photo-title">
      <h2
        className="text-sm font-semibold text-slate-500"
        id="profile-photo-title"
      >
        {tx("Profile photo")}
      </h2>
      {photoLoading ? (
        <p className="muted" role="status">
          {" "}
          {tx("Loading profile photo.")}{" "}
        </p>
      ) : null}
      {canView && photoUrl && !photoLoading ? (
        <img
          alt={tx("Profile photo of {name}", {
            name: safeMemberName(memberName),
          })}
          className="mx-auto mt-3 h-36 w-36 rounded-full object-cover ring-4 ring-slate-100"
          onError={() => {
            setPhotoUrl(null);
          }}
          src={photoUrl}
        />
      ) : null}
      {!photoLoading && !(canView && photoUrl) ? <EmptyProfilePhoto /> : null}
    </section>
  );
}
