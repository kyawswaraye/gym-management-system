import type { ReactNode } from "react";

const TITLE_CLASS =
  "text-3xl font-black tracking-tight text-slate-900 sm:text-4xl";

type PageHeaderProps = {
  title: string;
  titleId?: string;
  eyebrow?: string;
  description?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
};

export function PageHeader({
  title,
  titleId,
  eyebrow,
  description,
  actions,
  children,
}: PageHeaderProps) {
  const copy = (
    <>
      {eyebrow ? (
        <p className="text-xs font-bold tracking-[0.18em] text-amber-600 uppercase">
          {eyebrow}
        </p>
      ) : null}
      <h1
        className={eyebrow ? `mt-2 ${TITLE_CLASS}` : TITLE_CLASS}
        id={titleId}
      >
        {title}
      </h1>
      {description ? (
        <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
          {description}
        </p>
      ) : null}
    </>
  );

  return (
    <header className="mb-7 border-b border-slate-200 pb-6">
      {actions ? (
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
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
