"use client";
import { useTx } from "@/i18n/use-tx";

import Link from "next/link";
import { usePathname } from "next/navigation";
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
        strokeWidth="1.75"
      />
    </svg>
  );
}

const toggleClass =
  "inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-theme-md transition-colors motion-reduce:transition-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary";

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
    <span className="truncate tracking-tight text-white">
      {" "}
      {tx("GymPilot")}
      <span className="ml-1 font-semibold text-[#e8c460]">MM</span>
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
      className={`size-4 shrink-0 text-[rgb(244_247_251/55%)] transition-transform duration-150 motion-reduce:transition-none ${open ? "rotate-180" : ""}`}
      fill="none"
      viewBox="0 0 20 20"
    >
      <path
        d="M5.5 12.25 10 7.75l4.5 4.5"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.75"
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
        className={`flex w-full min-w-0 items-center gap-2 rounded-xl p-2 text-left transition-colors motion-reduce:transition-none hover:bg-[rgb(255_255_255/8%)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#e8c460] ${menuOpen ? "bg-[rgb(255_255_255/10%)]" : ""}`}
        type="button"
        onClick={toggleMenu}
      >
        <span
          aria-hidden="true"
          className="grid size-8 shrink-0 place-items-center rounded-full bg-[linear-gradient(145deg,#f8e7b0,#c9a24a)] text-[13px] font-semibold text-[#071e4a]"
        >
          {accountInitial(accountName)}
        </span>
        <span className="min-w-0 flex-1 leading-snug">
          <span className="block truncate text-sm font-semibold text-white">
            {accountName}
          </span>
          {email ? (
            <span className="block truncate text-caption text-[rgb(244_247_251/62%)]">
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
              className="z-50 rounded-theme-lg border border-border bg-surface p-1.5 shadow-overlay"
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
                <p className="min-w-0 truncate text-sm font-medium text-muted">
                  {tx("Language")}
                </p>
                <LanguageSwitch variant="inline" />
              </div>
              <div className="mx-2 my-1 h-px bg-border" />
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
  const assistantPage = usePathname() === "/assistant";
  const [open, setOpen] = useState(true);
  const [assistantMobileOpen, setAssistantMobileOpen] = useState(false);
  const mobileOpen = assistantPage ? assistantMobileOpen : open;
  const fullyClosed = !open && !mobileOpen;

  function hideSidebar() {
    setOpen(false);
    setAssistantMobileOpen(false);
  }

  function showSidebar() {
    setOpen(true);
    setAssistantMobileOpen(true);
  }

  const frame = (
    <>
      <aside
        aria-hidden={fullyClosed ? true : undefined}
        className={`app-sidebar relative z-30 text-[rgb(244_247_251/88%)] shadow-[8px_0_32px_rgb(7_30_74/18%)] motion-reduce:transition-none max-md:grid max-md:overflow-hidden max-md:border-b max-md:border-[rgb(255_255_255/10%)] max-md:transition-[grid-template-rows] max-md:duration-300 max-md:ease-in-out md:fixed md:inset-y-0 md:left-0 md:flex md:w-64 md:flex-col md:overflow-hidden md:border-r md:border-[rgb(255_255_255/10%)] md:transition-transform md:duration-300 md:ease-in-out ${mobileOpen ? "max-md:grid-rows-[1fr]" : "max-md:grid-rows-[0fr] max-md:border-b-0"} ${open ? "md:translate-x-0" : "md:-translate-x-full"} ${assistantPage && mobileOpen ? "max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:z-40 max-md:h-dvh max-md:w-[min(18rem,86vw)] max-md:max-h-none" : ""}`}
        id="app-sidebar"
        inert={fullyClosed ? true : undefined}
      >
        <div className="flex min-h-0 flex-col overflow-hidden p-6 md:h-full md:min-h-0 md:flex-1">
          <div className="mb-4 flex min-w-0 shrink-0 items-center gap-2 border-b border-[rgb(255_255_255/10%)] pb-4">
            <Link
              aria-label={
                title === "GymPilot MM" ? tx("GymPilot MM") : undefined
              }
              className="flex min-w-0 flex-1 items-center gap-2.5 text-sm font-semibold text-white"
              href={homePath}
              title={title}
            >
              {gymLogo ? (
                <GymLogo
                  className="h-9 w-9 shrink-0 rounded-theme-md object-cover"
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
              className={`${toggleClass} text-[rgb(244_247_251/70%)] hover:bg-[rgb(255_255_255/10%)] hover:text-white`}
              type="button"
              onClick={hideSidebar}
            >
              <MenuIcon />
            </button>
          </div>
          <div className="md:min-h-0 md:flex-1 md:overflow-y-auto">
            <SidebarNav entries={navigation} />
          </div>
          <footer className="mt-auto shrink-0 border-t border-[rgb(255_255_255/10%)] pt-3">
            <AccountMenu
              accountName={accountName}
              email={email}
              sidebarOpen={open}
            />
          </footer>
        </div>
      </aside>
      {assistantPage && mobileOpen ? (
        <button
          aria-hidden="true"
          className="fixed inset-0 z-30 bg-black/30 md:hidden"
          tabIndex={-1}
          type="button"
          onClick={() => setAssistantMobileOpen(false)}
        />
      ) : null}
      <button
        aria-controls="app-sidebar"
        aria-expanded="false"
        aria-hidden={open && mobileOpen ? true : undefined}
        aria-label={tx("Show sidebar")}
        className={`${toggleClass} fixed top-3 left-3 z-40 border border-border bg-surface text-text shadow-overlay transition-opacity duration-300 ease-in-out hover:bg-inset motion-reduce:transition-none ${open ? "md:pointer-events-none md:opacity-0" : "md:opacity-100"} ${mobileOpen ? "max-md:pointer-events-none max-md:opacity-0" : "max-md:opacity-100"}`}
        tabIndex={open && mobileOpen ? -1 : 0}
        type="button"
        onClick={showSidebar}
      >
        <MenuIcon />
      </button>
      <div
        className={`min-w-0 transition-[margin] duration-300 ease-in-out motion-reduce:transition-none ${open ? "md:ml-64" : ""} ${assistantPage ? "flex min-h-0 flex-1 flex-col" : ""}`}
      >
        <main
          className={
            assistantPage
              ? "flex min-h-0 w-full max-w-none flex-1 flex-col overflow-hidden bg-white p-0"
              : `mx-auto w-full max-w-[100rem] px-5 pb-5 transition-[padding] duration-300 ease-in-out md:px-8 md:pb-8 motion-reduce:transition-none ${open ? "pt-5 md:pt-8" : "pt-16"}`
          }
        >
          {children}
        </main>
      </div>
    </>
  );

  if (!assistantPage) return frame;

  return (
    <div
      className="flex h-dvh max-h-dvh flex-col overflow-hidden bg-white"
      data-app-sidebar={open ? "open" : "closed"}
    >
      {frame}
    </div>
  );
}
