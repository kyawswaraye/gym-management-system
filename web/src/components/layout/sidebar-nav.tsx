"use client";

import { useState } from "react";
import { usePathname } from "next/navigation";

import {
  NavLink,
  navLinkIsCurrent,
  navLinkMatches,
} from "@/components/layout/nav-link";
import {
  navigationHrefs,
  type NavEntry,
} from "@/components/layout/workspace-nav";

function folderId(label: string): string {
  return `sidebar-folder-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
}

function activeFolder(
  entries: readonly NavEntry[],
  pathname: string,
): string | null {
  for (const entry of entries) {
    if (entry.kind !== "folder") continue;
    if (entry.links.some((link) => navLinkMatches(pathname, link.href))) {
      return entry.label;
    }
  }
  return null;
}

function folderClass(open: boolean, current: boolean): string {
  const shared =
    "flex w-full items-center justify-between gap-3 rounded-xl border px-3 py-2.5 text-left text-sm transition-colors duration-150 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#0866ff]";
  if (current) {
    return `${shared} border-transparent bg-[#e7f3ff] font-semibold text-[#0866ff]`;
  }
  if (open) {
    return `${shared} border-transparent bg-transparent font-semibold text-[#0866ff]`;
  }
  return `${shared} border-transparent font-medium text-[#050505] hover:bg-[#f2f2f2]`;
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      aria-hidden="true"
      className={`h-4 w-4 shrink-0 text-[#65676b] transition-transform duration-150 ${open ? "rotate-90" : ""}`}
      viewBox="0 0 20 20"
      fill="none"
    >
      <path
        d="M7.5 4.5 13 10l-5.5 5.5"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function SidebarNav({ entries }: { entries: readonly NavEntry[] }) {
  const pathname = usePathname() ?? "";
  const currentFolder = activeFolder(entries, pathname);
  const [open, setOpen] = useState<string | null>(currentFolder);
  const [seenPath, setSeenPath] = useState(pathname);
  if (seenPath !== pathname) {
    setSeenPath(pathname);
    setOpen(currentFolder);
  }
  const hrefs = navigationHrefs(entries);

  return (
    <nav aria-label="Primary" className="mt-4 grid gap-1">
      {entries.map((entry) => {
        if (entry.kind === "link") {
          return (
            <NavLink href={entry.href} key={entry.href} peers={hrefs}>
              {entry.label}
            </NavLink>
          );
        }
        const expanded = open === entry.label;
        const current = entry.links.some((link) =>
          navLinkIsCurrent(pathname, link.href, hrefs),
        );
        return (
          <div className="grid gap-1" key={entry.label}>
            <button
              aria-controls={folderId(entry.label)}
              aria-expanded={expanded}
              className={folderClass(expanded, current && !expanded)}
              type="button"
              onClick={() =>
                setOpen((currentOpen) =>
                  currentOpen === entry.label ? null : entry.label,
                )
              }
            >
              <span className="truncate">{entry.label}</span>
              <Chevron open={expanded} />
            </button>
            {expanded ? (
              <div
                className="grid gap-1 pl-3"
                id={folderId(entry.label)}
                role="group"
                aria-label={entry.label}
              >
                {entry.links.map((link) => (
                  <NavLink href={link.href} key={link.href} peers={hrefs}>
                    {link.label}
                  </NavLink>
                ))}
              </div>
            ) : null}
          </div>
        );
      })}
    </nav>
  );
}
