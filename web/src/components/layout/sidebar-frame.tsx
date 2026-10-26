"use client";

import Image from "next/image";
import Link from "next/link";
import { useState, type ReactNode } from "react";

import { GymLogo } from "@/components/gym-logo";
import { SidebarNav } from "@/components/layout/sidebar-nav";
import type { NavEntry } from "@/components/layout/workspace-nav";
import { LogoutButton } from "@/features/auth/logout-button";

function MenuIcon() {
  return (
    <svg aria-hidden="true" className="h-5 w-5" fill="none" viewBox="0 0 20 20">
      <path
        d="M3.5 5.5h13M3.5 10h13M3.5 14.5h13"
        stroke="currentColor"
        strokeLinecap="round"
        strokeWidth="1.8"
      />
    </svg>
  );
}

const toggleClass =
  "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#0866ff]";

function SidebarTitle({ title }: { title: string }) {
  if (title !== "GymPilot MM") {
    return <span className="truncate">{title}</span>;
  }
  return (
    <span className="truncate tracking-tight">
      GymPilot<span className="ml-1 font-bold text-[#0866ff]">MM</span>
    </span>
  );
}

export function SidebarFrame({
  title,
  homePath,
  navigation,
  accountName,
  email,
  gymLogo = false,
  children,
}: {
  title: string;
  homePath: string;
  navigation: readonly NavEntry[];
  accountName: string;
  email: string | null;
  gymLogo?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(true);

  return (
    <>
      <aside
        aria-hidden={open ? undefined : true}
        className={`z-30 bg-white text-[#050505] shadow-sm motion-reduce:transition-none max-md:grid max-md:overflow-hidden max-md:border-b max-md:border-[#e4e6eb] max-md:transition-[grid-template-rows] max-md:duration-300 max-md:ease-in-out md:fixed md:inset-y-0 md:left-0 md:flex md:w-64 md:flex-col md:overflow-hidden md:border-r md:border-[#e4e6eb] md:transition-[transform,box-shadow] md:duration-300 md:ease-in-out ${open ? "max-md:grid-rows-[1fr] md:translate-x-0" : "max-md:grid-rows-[0fr] max-md:border-b-0 md:-translate-x-full md:shadow-none"}`}
        id="app-sidebar"
        inert={open ? undefined : true}
      >
        <div className="flex min-h-0 flex-col overflow-hidden p-4 md:h-full md:min-h-0 md:flex-1 md:p-6">
          <div className="flex min-w-0 shrink-0 items-center gap-2 border-b border-[#e4e6eb] pb-4">
            <Link
              aria-label={title === "GymPilot MM" ? "GymPilot MM" : undefined}
              className="flex min-w-0 flex-1 items-center gap-2.5 text-sm font-semibold text-[#050505]"
              href={homePath}
              title={title}
            >
              {gymLogo ? (
                <GymLogo
                  className="h-9 w-9 shrink-0 rounded-lg object-cover"
                  fallback={
                    <Image
                      src="/brand/gympilot-mark.png"
                      alt=""
                      width={36}
                      height={36}
                      className="h-9 w-9 shrink-0"
                    />
                  }
                />
              ) : (
                <Image
                  src="/brand/gympilot-mark.png"
                  alt=""
                  width={36}
                  height={36}
                  className="h-9 w-9 shrink-0"
                />
              )}
              <SidebarTitle title={title} />
            </Link>
            <button
              aria-controls="app-sidebar"
              aria-expanded="true"
              aria-label="Hide sidebar"
              className={`${toggleClass} text-[#65676b] hover:bg-[#f2f2f2] hover:text-[#050505]`}
              type="button"
              onClick={() => setOpen(false)}
            >
              <MenuIcon />
            </button>
          </div>
          <div className="md:min-h-0 md:flex-1 md:overflow-y-auto">
            <SidebarNav entries={navigation} />
          </div>
          <footer className="mt-4 shrink-0 border-t border-[#e4e6eb] pt-4">
            <p className="truncate text-sm font-semibold text-[#050505]">
              {accountName}
            </p>
            {email ? (
              <p className="mt-1 truncate text-xs text-[#65676b]">{email}</p>
            ) : null}
            <LogoutButton className="mt-2" />
          </footer>
        </div>
      </aside>
      <button
        aria-controls="app-sidebar"
        aria-expanded="false"
        aria-hidden={open ? true : undefined}
        aria-label="Show sidebar"
        className={`${toggleClass} fixed top-3 left-3 z-40 h-9 w-9 border border-[#e4e6eb] bg-white text-[#050505] shadow-md transition-opacity duration-300 ease-in-out hover:bg-[#f2f2f2] motion-reduce:transition-none ${open ? "pointer-events-none opacity-0" : "opacity-100"}`}
        tabIndex={open ? -1 : 0}
        type="button"
        onClick={() => setOpen(true)}
      >
        <MenuIcon />
      </button>
      <div
        className={`min-w-0 transition-[margin] duration-300 ease-in-out motion-reduce:transition-none ${open ? "md:ml-64" : ""}`}
      >
        <main
          className={`mx-auto w-full max-w-[100rem] px-5 pb-5 transition-[padding] duration-300 ease-in-out md:px-8 md:pb-8 motion-reduce:transition-none ${open ? "pt-5 md:pt-8" : "pt-16"}`}
        >
          {children}
        </main>
      </div>
    </>
  );
}
