"use client";

import { useEffect, useRef, useState } from "react";

import {
  collectLookupPages,
  LOOKUP_PAGE_CAP,
  LOOKUP_PAGE_LIMIT,
} from "@/features/finance/paging";
import { phase5Error } from "@/features/pt/errors";
import type { CollectionPage } from "@/features/operations/client";

export { collectLookupPages, LOOKUP_PAGE_CAP, LOOKUP_PAGE_LIMIT };

export function shortId(id: string): string {
  return id.slice(0, 8);
}

export function useLookupOptions<T extends { id: string }>(
  filterKey: string,
  load: (after?: string) => Promise<CollectionPage<T>>,
  enabled = true,
) {
  const loadRef = useRef(load);
  const [items, setItems] = useState<T[]>([]);
  const [error, setError] = useState("");
  const [bounded, setBounded] = useState(false);
  const [pending, setPending] = useState(enabled);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    loadRef.current = load;
  }, [load]);

  useEffect(() => {
    if (!enabled) {
      const timer = window.setTimeout(() => {
        setItems([]);
        setError("");
        setBounded(false);
        setPending(false);
      }, 0);
      return () => window.clearTimeout(timer);
    }
    let active = true;
    const timer = window.setTimeout(() => {
      setPending(true);
      void collectLookupPages((after) => loadRef.current(after))
        .then((result) => {
          if (!active) return;
          setItems(result.error && !result.bounded ? [] : result.items);
          setBounded(result.bounded);
          setError(result.error);
        })
        .catch((caught: unknown) => {
          if (!active) return;
          setItems([]);
          setBounded(false);
          setError(phase5Error(caught, "The choices could not be loaded."));
        })
        .finally(() => {
          if (active) setPending(false);
        });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [enabled, filterKey, reloadKey]);

  return {
    items,
    error,
    bounded,
    pending,
    reload: () => setReloadKey((current) => current + 1),
  };
}
