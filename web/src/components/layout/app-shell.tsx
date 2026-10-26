import type { ReactNode } from "react";

import { SidebarFrame } from "@/components/layout/sidebar-frame";
import { workspaceNavigation } from "@/components/layout/workspace-nav";

import { SessionKeepalive } from "@/features/auth/session-keepalive";
import { platformHomePath, tenantHomePath } from "@/lib/auth/authorization";
import type { CurrentUser } from "@/types/api";

export function AppShell({
  session,
  children,
  platform = false,
}: {
  session: CurrentUser;
  children: ReactNode;
  platform?: boolean;
}) {
  const gymName = session.gym?.name ?? "Selected gym";
  const homePath = platform
    ? platformHomePath(session)
    : tenantHomePath(session);
  const navigation = workspaceNavigation(session, platform);
  const accountName = session.user.first_name || session.user.email;
  const title = platform ? "GymPilot MM" : gymName;

  return (
    <div className="min-h-screen bg-[#f0f2f5]">
      <SessionKeepalive />
      <SidebarFrame
        accountName={accountName}
        email={session.user.first_name ? session.user.email : null}
        homePath={homePath}
        gymLogo={!platform}
        navigation={navigation}
        title={title}
      >
        {children}
      </SidebarFrame>
    </div>
  );
}
