"use client";

import { useEffect, useRef, useState } from "react";

import { financeError } from "@/features/finance/errors";
import type { CollectionPage } from "@/features/operations/client";

const PAGE_CAP = 20;

export function useCursorPages<T>(
  filterKey: string,
  load: (after?: string) => Promise<CollectionPage<T>>,
) {
  const loadRef = useRef(load);
  const [rows, setRows] = useState<T[]>([]);
  const [cursors, setCursors] = useState<string[]>([""]);
  const [index, setIndex] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [nextAfter, setNextAfter] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(true);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    loadRef.current = load;
  }, [load]);

  useEffect(() => {
    let active = true;
    const timer = window.setTimeout(() => {
      setPending(true);
      void loadRef
        .current(undefined)
        .then((page) => {
          if (!active) return;
          setRows(page.data);
          setCursors([""]);
          setIndex(0);
          setHasMore(page.meta.has_more);
          setNextAfter(page.meta.next_after);
          setError("");
        })
        .catch((caught: unknown) => {
          if (!active) return;
          setRows([]);
          setHasMore(false);
          setNextAfter(null);
          setError(financeError(caught, "The list could not be loaded."));
        })
        .finally(() => {
          if (active) setPending(false);
        });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [filterKey, reloadKey]);

  async function goNext() {
    if (!hasMore || !nextAfter || index + 1 >= PAGE_CAP) return;
    setPending(true);
    try {
      const page = await loadRef.current(nextAfter);
      setRows(page.data);
      setCursors((current) => [...current, nextAfter]);
      setIndex((current) => current + 1);
      setHasMore(page.meta.has_more);
      setNextAfter(page.meta.next_after);
      setError("");
    } catch (caught) {
      setError(financeError(caught, "The next page could not be loaded."));
    } finally {
      setPending(false);
    }
  }

  async function goPrevious() {
    if (index === 0) return;
    const cursor = cursors[index - 1] ?? "";
    setPending(true);
    try {
      const page = await loadRef.current(cursor || undefined);
      setRows(page.data);
      setIndex((current) => current - 1);
      setHasMore(page.meta.has_more);
      setNextAfter(page.meta.next_after);
      setError("");
    } catch (caught) {
      setError(financeError(caught, "The previous page could not be loaded."));
    } finally {
      setPending(false);
    }
  }

  return {
    rows,
    error,
    pending,
    hasMore: hasMore && index + 1 < PAGE_CAP,
    bounded: hasMore && index + 1 >= PAGE_CAP,
    index,
    goNext,
    goPrevious,
    reload: () => setReloadKey((current) => current + 1),
  };
}
