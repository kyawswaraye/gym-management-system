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

const focusClass =
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary";

function navLinkClass(current: boolean, parent: boolean): string {
  const shared = `shrink-0 rounded-theme-md border px-3 py-2.5 ${parent ? "text-sm/8" : "text-sm"} font-medium text-text transition-colors duration-150 motion-reduce:transition-none ${focusClass}`;
  if (current) {
    return `${shared} border-primary bg-primary-soft font-semibold text-primary-hover hover:bg-primary-soft`;
  }
  return `${shared} border-transparent hover:bg-inset`;
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
