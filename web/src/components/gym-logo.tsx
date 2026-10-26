"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

export function GymLogo({
  revision = 0,
  className,
  fallback,
}: {
  revision?: number;
  className: string;
  fallback: ReactNode;
}) {
  const [src, setSrc] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const shownUrl = useRef("");

  useEffect(() => {
    let active = true;
    void fetch("/api/operations/gym/logo", {
      credentials: "same-origin",
      cache: "no-store",
    })
      .then(async (response) => {
        if (!response.ok) return null;
        const header = response.headers.get("content-type") ?? "";
        const blob = await response.blob();
        const type = header || blob.type;
        if (!type.startsWith("image/")) return null;
        return blob;
      })
      .then((blob) => {
        if (!active) return;
        if (!blob) {
          if (shownUrl.current) URL.revokeObjectURL(shownUrl.current);
          shownUrl.current = "";
          setSrc(null);
          return;
        }
        const next = URL.createObjectURL(blob);
        if (shownUrl.current) URL.revokeObjectURL(shownUrl.current);
        shownUrl.current = next;
        setSrc(next);
      })
      .catch(() => {
        if (active) setSrc(null);
      });
    return () => {
      active = false;
    };
  }, [revision, tick]);

  useEffect(
    () => () => {
      if (shownUrl.current) URL.revokeObjectURL(shownUrl.current);
    },
    [],
  );

  useEffect(() => {
    const refresh = () => setTick((value) => value + 1);
    window.addEventListener("gym-logo-updated", refresh);
    return () => window.removeEventListener("gym-logo-updated", refresh);
  }, []);

  if (!src) return fallback;
  return <img alt="" className={className} src={src} />;
}
