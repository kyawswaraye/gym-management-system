"use client";
import { useTx } from "@/i18n/use-tx";

import { useRef, useState, type KeyboardEvent } from "react";
import { useRouter } from "next/navigation";

import { CheckInDisplay } from "@/features/operations/check-in-display";
import { CheckInsManager } from "@/features/operations/check-ins-manager";
import {
  checkInModeHref,
  checkInWorkspaceMode,
  type CheckInWorkspaceMode,
} from "@/features/operations/check-ins-mode";

const MODES = ["desk", "qr"] as const satisfies readonly CheckInWorkspaceMode[];

const MODE_LABEL: Record<CheckInWorkspaceMode, string> = {
  desk: "Desk check-ins",
  qr: "QR display",
};

const MODE_HINT: Record<CheckInWorkspaceMode, string> = {
  desk: "Search, check in, and check out.",
  qr: "Rotating code on this screen.",
};

function tabClass(selected: boolean): string {
  const shared =
    "flex h-full min-h-[4.75rem] flex-col items-center justify-center gap-1.5 rounded-xl px-2 py-3 text-center transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 sm:px-4";
  return selected
    ? `${shared} bg-white text-slate-900 shadow-sm ring-1 ring-slate-200`
    : `${shared} text-slate-600 hover:bg-white/80 hover:text-slate-900`;
}

export function CheckInsWorkspace({
  mode: authorizedMode,
  canDesk,
  canQr,
  canCreate,
  canUpdate,
  canScan = false,
  membershipId = "",
  timeZone,
}: {
  mode: CheckInWorkspaceMode;
  canDesk: boolean;
  canQr: boolean;
  canCreate: boolean;
  canUpdate: boolean;
  canScan?: boolean;
  membershipId?: string;
  timeZone: string;
}) {
  const tx = useTx();
  const router = useRouter();
  const tabs = useRef<
    Partial<Record<CheckInWorkspaceMode, HTMLButtonElement | null>>
  >({});
  const [mode, setMode] = useState(authorizedMode);
  const [syncedMode, setSyncedMode] = useState(authorizedMode);
  if (authorizedMode !== syncedMode) {
    setSyncedMode(authorizedMode);
    setMode(authorizedMode);
  }
  const activeMode = authorizedMode !== syncedMode ? authorizedMode : mode;
  const shown = checkInWorkspaceMode({
    canDesk,
    canQr,
    view: activeMode === "qr" ? "qr" : null,
  });
  const showTabs = canDesk && canQr;

  function select(next: CheckInWorkspaceMode) {
    const visible = checkInWorkspaceMode({
      canDesk,
      canQr,
      view: next === "qr" ? "qr" : null,
    });
    if (!visible || visible === shown) return;
    setMode(visible);
    router.replace(checkInModeHref(visible), { scroll: false });
    tabs.current[visible]?.focus();
  }

  function onTabsKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (!shown) return;
    const index = MODES.indexOf(shown);
    let next: CheckInWorkspaceMode | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      next = MODES[(index + 1) % MODES.length] ?? null;
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      next = MODES[(index - 1 + MODES.length) % MODES.length] ?? null;
    } else if (event.key === "Home") {
      next = "desk";
    } else if (event.key === "End") {
      next = "qr";
    }
    if (!next) return;
    event.preventDefault();
    select(next);
  }

  return (
    <div className="grid gap-6">
      {showTabs ? (
        <div className="max-w-2xl">
          <div
            aria-label={tx("Check-in mode")}
            className="grid grid-cols-2 gap-1 rounded-2xl border border-slate-200 bg-slate-100 p-1 shadow-sm"
            onKeyDown={onTabsKeyDown}
            role="tablist"
          >
            {MODES.map((item) => {
              const selected = shown === item;
              return (
                <button
                  aria-controls="check-in-workspace-panel"
                  aria-describedby={`check-in-tab-${item}-hint`}
                  aria-label={tx(MODE_LABEL[item])}
                  aria-selected={selected}
                  className={tabClass(selected)}
                  id={`check-in-tab-${item}`}
                  key={item}
                  onClick={() => select(item)}
                  ref={(node) => {
                    tabs.current[item] = node;
                  }}
                  role="tab"
                  tabIndex={selected ? 0 : -1}
                  type="button"
                >
                  <span
                    aria-hidden="true"
                    className={`h-1 w-8 rounded-full ${
                      selected ? "bg-amber-400" : "bg-slate-300"
                    }`}
                  />
                  <span className="text-sm font-bold">
                    {tx(MODE_LABEL[item])}
                  </span>
                  <span
                    className="text-xs font-medium leading-4 text-balance text-slate-500"
                    id={`check-in-tab-${item}-hint`}
                  >
                    {tx(MODE_HINT[item])}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      ) : null}
      <div
        aria-labelledby={
          showTabs && shown ? `check-in-tab-${shown}` : undefined
        }
        id="check-in-workspace-panel"
        role={showTabs ? "tabpanel" : undefined}
      >
        {shown === "desk" ? (
          <CheckInsManager
            canCreate={canCreate}
            canScan={canScan}
            canUpdate={canUpdate}
            membershipId={membershipId}
            timeZone={timeZone}
          />
        ) : null}
        {shown === "qr" ? <CheckInDisplay /> : null}
      </div>
    </div>
  );
}
