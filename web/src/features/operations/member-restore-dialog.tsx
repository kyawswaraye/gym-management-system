"use client";

import { useId } from "react";

import type { Member } from "@/types/api";

export function memberDisplayName(member: Member): string {
  return (
    `${member.first_name} ${member.last_name}`.trim() || member.member_number
  );
}

export function memberArchiveIdentity(member: Member): string {
  const name = `${member.first_name} ${member.last_name}`.trim();
  return name ? `${name} (${member.member_number})` : member.member_number;
}

export function RestoreMemberDialog({
  identity,
  onCancel,
  onConfirm,
}: {
  identity: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const titleId = useId();
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center p-4"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onCancel();
      }}
    >
      <section
        aria-labelledby={titleId}
        aria-modal="true"
        className="w-full max-w-md overflow-hidden"
        role="dialog"
        onKeyDown={(event) => {
          if (event.key === "Escape") onCancel();
        }}
      >
        <div className="border-b border-slate-200 px-5 py-4">
          <p className="text-xs font-bold tracking-[0.16em] text-teal-700 uppercase">
            Restore member
          </p>
          <h2
            className="mt-1 text-xl font-black tracking-tight text-slate-900"
            id={titleId}
          >
            Restore {identity}?
          </h2>
        </div>
        <div className="p-5">
          <p className="text-sm leading-6 text-slate-600">
            This member becomes active again. Membership and payment history
            stay as they are. The member record is not deleted.
          </p>
          <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-sm font-semibold text-slate-700">
            {identity}
          </p>
          <div className="mt-5 flex justify-end">
            <button
              className="button button-cancel"
              onClick={onCancel}
              type="button"
            >
              Cancel
            </button>
            <button
              className="button button-success"
              onClick={onConfirm}
              type="button"
            >
              Restore
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

export function ArchiveMemberDialog({
  identity,
  onCancel,
  onConfirm,
}: {
  identity: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const titleId = useId();
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center p-4"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onCancel();
      }}
    >
      <section
        aria-labelledby={titleId}
        aria-modal="true"
        className="w-full max-w-md overflow-hidden"
        role="dialog"
        onKeyDown={(event) => {
          if (event.key === "Escape") onCancel();
        }}
      >
        <div className="border-b border-slate-200 px-5 py-4">
          <p className="text-xs font-bold tracking-[0.16em] text-red-600 uppercase">
            Archive member
          </p>
          <h2
            className="mt-1 text-xl font-black tracking-tight text-slate-900"
            id={titleId}
          >
            Archive {identity}?
          </h2>
        </div>
        <div className="p-5">
          <p className="text-sm leading-6 text-slate-600">
            This member will no longer appear as active. Membership and payment
            history remain. The member record is not deleted.
          </p>
          <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-sm font-semibold text-slate-700">
            {identity}
          </p>
          <div className="mt-5 flex justify-end">
            <button
              className="button button-cancel"
              onClick={onCancel}
              type="button"
            >
              Cancel
            </button>
            <button
              className="button button-archive"
              onClick={onConfirm}
              type="button"
            >
              Archive
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
