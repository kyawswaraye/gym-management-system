import { createNavigationContainerRef } from "@react-navigation/native";

import type { MainTabParamList } from "./types";

export const navigationRef = createNavigationContainerRef<MainTabParamList>();

let queued: { id: string | null } | null = null;

export function openInbox(notificationId: string | null): void {
  if (!navigationRef.isReady()) {
    queued = { id: notificationId };
    return;
  }
  queued = null;
  navigationRef.navigate("Inbox", {
    screen: "Inbox",
    params: notificationId ? { notificationId } : {},
  });
}

export function flushInboxLink(): void {
  if (!queued || !navigationRef.isReady()) {
    return;
  }
  openInbox(queued.id);
}
