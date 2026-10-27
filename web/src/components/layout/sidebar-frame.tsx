"use client";
import { useTx } from "@/i18n/use-tx";

import Link from "next/link";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { GymLogo } from "@/components/gym-logo";
import { SidebarNav } from "@/components/layout/sidebar-nav";
import type { NavEntry } from "@/components/layout/workspace-nav";
import { LogoutButton } from "@/features/auth/logout-button";
import { LanguageSwitch } from "@/i18n/language-switch";

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

function accountInitial(name: string): string {
  const trimmed = name.trim();
  return trimmed ? trimmed.slice(0, 1).toUpperCase() : "?";
}

function SidebarTitle({ title }: { title: string }) {
  const tx = useTx();
  if (title !== "GymPilot MM") {
    return <span className="truncate">{tx(title)}</span>;
  }
  return (
    <span className="truncate tracking-tight">
      {" "}
      {tx("GymPilot")}
      <span className="ml-1 font-bold text-[#0866ff]">MM</span>
    </span>
  );
}

type MenuAnchor = {
  left: number;
  width: number;
  top?: number;
  bottom?: number;
};

function menuAnchor(button: HTMLButtonElement): MenuAnchor {
  const rect = button.getBoundingClientRect();
  const shared = { left: rect.left, width: rect.width };
  if (rect.top >= 148) {
    return { ...shared, bottom: window.innerHeight - rect.top + 8 };
  }
  return { ...shared, top: rect.bottom + 8 };
}

function AccountChevron({ open }: { open: boolean }) {
  return (
    <svg
      aria-hidden="true"
      className={`size-4 shrink-0 text-slate-400 transition-transform duration-150 ${open ? "rotate-180" : ""}`}
      fill="none"
      viewBox="0 0 20 20"
    >
      <path
        d="M5.5 12.25 10 7.75l4.5 4.5"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.6"
      />
    </svg>
  );
}

function AccountMenu({
  accountName,
  email,
  sidebarOpen,
}: {
  accountName: string;
  email: string | null;
  sidebarOpen: boolean;
}) {
  const tx = useTx();
  const menuId = useId();
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [anchor, setAnchor] = useState<MenuAnchor | null>(null);
  const [sidebarWasOpen, setSidebarWasOpen] = useState(sidebarOpen);
  if (sidebarOpen !== sidebarWasOpen) {
    setSidebarWasOpen(sidebarOpen);
    if (!sidebarOpen && menuOpen) setMenuOpen(false);
  }

  useEffect(() => {
    if (!menuOpen) return;
    function place() {
      if (buttonRef.current) setAnchor(menuAnchor(buttonRef.current));
    }
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setMenuOpen(false);
      buttonRef.current?.focus();
    }
    function onPointer(event: MouseEvent) {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (
        buttonRef.current?.contains(target) ||
        menuRef.current?.contains(target)
      ) {
        return;
      }
      setMenuOpen(false);
    }
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPointer);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPointer);
    };
  }, [menuOpen]);

  function toggleMenu() {
    if (menuOpen) {
      setMenuOpen(false);
      return;
    }
    if (buttonRef.current) setAnchor(menuAnchor(buttonRef.current));
    setMenuOpen(true);
  }

  return (
    <>
      <button
        ref={buttonRef}
        aria-controls={menuId}
        aria-expanded={menuOpen}
        aria-haspopup="dialog"
        aria-label={`${tx("Account")}, ${accountName}`}
        className={`flex w-full min-w-0 items-center gap-2.5 rounded-xl px-2 py-2 text-left transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#0866ff] ${menuOpen ? "bg-[#f2f2f2]" : "hover:bg-[#f2f2f2]"}`}
        type="button"
        onClick={toggleMenu}
      >
        <span
          aria-hidden="true"
          className="grid size-9 shrink-0 place-items-center rounded-full bg-[#e7f3ff] text-sm font-semibold text-[#0866ff]"
        >
          {accountInitial(accountName)}
        </span>
        <span className="min-w-0 flex-1 leading-snug">
          <span className="block truncate text-sm font-semibold text-[#050505]">
            {accountName}
          </span>
          {email ? (
            <span className="block truncate text-[0.7rem] text-slate-500">
              {email}
            </span>
          ) : null}
        </span>
        <AccountChevron open={menuOpen} />
      </button>
      {menuOpen && anchor
        ? createPortal(
            <div
              ref={menuRef}
              aria-label={tx("Account")}
              className="z-50 rounded-2xl border border-[#e4e6eb] bg-white p-1.5 shadow-[0_12px_32px_rgba(5,5,5,0.14)]"
              id={menuId}
              role="dialog"
              style={{
                position: "fixed",
                left: anchor.left,
                width: anchor.width,
                top: anchor.top,
                bottom: anchor.bottom,
              }}
              tabIndex={-1}
            >
              <div className="flex items-center justify-between gap-3 px-2.5 py-2">
                <p className="min-w-0 truncate text-sm font-medium text-slate-500">
                  {tx("Language")}
                </p>
                <LanguageSwitch variant="inline" />
              </div>
              <div className="mx-2 my-1 h-px bg-[#e4e6eb]" />
              <LogoutButton />
            </div>,
            document.body,
          )
        : null}
    </>
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
  const tx = useTx();
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
              aria-label={
                title === "GymPilot MM" ? tx("GymPilot MM") : undefined
              }
              className="flex min-w-0 flex-1 items-center gap-2.5 text-sm font-semibold text-[#050505]"
              href={homePath}
              title={title}
            >
              {gymLogo ? (
                <GymLogo
                  className="h-9 w-9 shrink-0 rounded-lg object-cover"
                  fallback={
                    <img
                      src="/brand/gympilot-mark.png"
                      alt=""
                      width={36}
                      height={36}
                      className="h-9 w-9 shrink-0"
                    />
                  }
                />
              ) : (
                <img
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
              aria-label={tx("Hide sidebar")}
              className={`${toggleClass} text-slate-500 hover:bg-[#f2f2f2] hover:text-[#050505]`}
              type="button"
              onClick={() => setOpen(false)}
            >
              <MenuIcon />
            </button>
          </div>
          <div className="md:min-h-0 md:flex-1 md:overflow-y-auto">
            <SidebarNav entries={navigation} />
          </div>
          <footer className="mt-auto shrink-0 border-t border-[#e4e6eb] pt-3">
            <AccountMenu
              accountName={accountName}
              email={email}
              sidebarOpen={open}
            />
          </footer>
        </div>
      </aside>
      <button
        aria-controls="app-sidebar"
        aria-expanded="false"
        aria-hidden={open ? true : undefined}
        aria-label={tx("Show sidebar")}
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
