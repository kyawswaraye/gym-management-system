"use client";

import type { ReactNode } from "react";

import { useLocale, useTx } from "@/i18n/use-tx";

const TITLE_CLASS = "text-display text-text";

type PageHeaderProps = {
  title: string;
  titleId?: string;
  eyebrow?: string;
  description?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  compact?: boolean;
  tone?: "plain" | "premium";
};

export function PageHeader({
  title,
  titleId,
  eyebrow,
  description,
  actions,
  children,
  compact = false,
  tone = "plain",
}: PageHeaderProps) {
  const tx = useTx();
  const locale = useLocale();
  const premium = tone === "premium";
  const copy = compact ? (
    <h1 className="text-title text-text" id={titleId}>
      {tx(title)}
    </h1>
  ) : (
    <>
      {eyebrow ? (
        <p
          className={`text-xs font-semibold ${premium ? "text-[#f8e7b0]" : "text-primary-hover"} ${locale === "en" ? "tracking-[0.06em] uppercase" : "tracking-normal"}`}
        >
          {tx(eyebrow)}
        </p>
      ) : null}
      <h1
        className={`${eyebrow ? "mt-1 " : ""}${premium ? "text-display text-white" : TITLE_CLASS}`}
        id={titleId}
      >
        {tx(title)}
      </h1>
      {description ? (
        <p
          className={`mt-2 max-w-[640px] text-body ${premium ? "text-[rgb(244_247_251/78%)]" : "text-muted"}`}
        >
          {typeof description === "string" ? tx(description) : description}
        </p>
      ) : null}
    </>
  );

  return (
    <header
      className={
        compact
          ? "page-header page-header--compact"
          : premium
            ? "page-header page-header--premium"
            : "page-header"
      }
    >
      {actions ? (
        <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-end sm:justify-between">
          <div>{copy}</div>
          {actions}
        </div>
      ) : (
        copy
      )}
      {children}
    </header>
  );
}
