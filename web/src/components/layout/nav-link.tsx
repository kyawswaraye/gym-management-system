"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

function normalizePath(value: string): string {
  const path = value.split(/[?#]/, 1)[0] ?? "";
  if (path.length > 1 && path.endsWith("/")) return path.slice(0, -1);
  return path;
}

export function navLinkMatches(pathname: string, href: string): boolean {
  const current = normalizePath(pathname);
  const target = normalizePath(href);
  if (!target) return false;
  if (current === target) return true;
  if (target === "/") return false;
  return current.startsWith(`${target}/`);
}

export function navLinkIsCurrent(
  pathname: string,
  href: string,
  peers: readonly string[] = [],
): boolean {
  if (!navLinkMatches(pathname, href)) return false;
  const target = normalizePath(href);
  return !peers.some((peer) => {
    const other = normalizePath(peer);
    return other.length > target.length && navLinkMatches(pathname, peer);
  });
}

function navLinkClass(current: boolean): string {
  const shared =
    "shrink-0 rounded-xl border px-3 py-2.5 text-sm transition-colors duration-150 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber-300";
  if (current) {
    return `${shared} border-amber-300/50 bg-amber-400/20 font-semibold text-amber-50 shadow-[inset_3px_0_0_#fbbf24] hover:border-amber-300/70 hover:bg-amber-400/30 hover:text-white`;
  }
  return `${shared} border-transparent font-medium text-slate-200 hover:border-amber-300/20 hover:bg-white/10 hover:text-white`;
}

export function NavLink({
  href,
  peers = [],
  children,
}: {
  href: string;
  peers?: readonly string[];
  children: ReactNode;
}) {
  const pathname = usePathname() ?? "";
  const current = navLinkIsCurrent(pathname, href, peers);
  return (
    <Link
      aria-current={current ? "page" : undefined}
      className={navLinkClass(current)}
      href={href}
    >
      {children}
    </Link>
  );
}
