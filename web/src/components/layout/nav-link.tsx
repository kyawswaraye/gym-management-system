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

function navLinkClass(current: boolean, parent: boolean): string {
  const shared = `shrink-0 rounded-xl border px-3 py-2.5 ${parent ? "text-sm/8" : "text-sm"} transition-colors duration-150 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#0866ff]`;
  if (current) {
    return `${shared} border-transparent bg-[#e7f3ff] font-semibold text-[#0866ff] hover:bg-[#d6e9ff]`;
  }
  return `${shared} border-transparent font-medium text-[#050505] hover:bg-[#f2f2f2]`;
}

export function NavLink({
  href,
  peers = [],
  parent = false,
  children,
}: {
  href: string;
  peers?: readonly string[];
  parent?: boolean;
  children: ReactNode;
}) {
  const pathname = usePathname() ?? "";
  const current = navLinkIsCurrent(pathname, href, peers);
  return (
    <Link
      aria-current={current ? "page" : undefined}
      className={navLinkClass(current, parent)}
      href={href}
    >
      {children}
    </Link>
  );
}
