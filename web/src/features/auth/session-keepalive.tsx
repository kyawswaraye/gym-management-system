"use client";

import { useEffect, useRef } from "react";

import { getCsrfToken, mutationHeaders } from "@/features/auth/client";

const REFRESH_INTERVAL_MS = 8 * 60 * 1000;

export function SessionKeepalive() {
  const refreshing = useRef<Promise<void> | null>(null);
  const lastAttempt = useRef(0);

  useEffect(() => {
    lastAttempt.current = Date.now();
    const refresh = () => {
      if (refreshing.current) return refreshing.current;
      lastAttempt.current = Date.now();
      refreshing.current = (async () => {
        try {
          const csrf = await getCsrfToken();
          await fetch("/api/auth/refresh", {
            method: "POST",
            credentials: "same-origin",
            headers: mutationHeaders(csrf),
            body: "{}",
          });
        } catch {
          // A later authenticated request will surface an expired session.
        }
      })().finally(() => {
        refreshing.current = null;
      });
      return refreshing.current;
    };

    const interval = window.setInterval(() => {
      void refresh();
    }, REFRESH_INTERVAL_MS);
    const onVisibilityChange = () => {
      if (
        document.visibilityState === "visible" &&
        Date.now() - lastAttempt.current >= REFRESH_INTERVAL_MS
      ) {
        void refresh();
      }
    };
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, []);

  return null;
}
