import { useInfiniteQuery } from "@tanstack/react-query";

import type { Page } from "../api/memberApi";
import { cursorAfter } from "./cursor";

export function useCursorList<T>(
  queryKey: readonly unknown[],
  load: (after?: string) => Promise<Page<T>>,
) {
  const query = useInfiniteQuery({
    queryKey,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => load(pageParam),
    getNextPageParam: (lastPage) => cursorAfter(lastPage?.meta) ?? undefined,
  });
  const items =
    query.data?.pages?.flatMap((page) =>
      Array.isArray(page?.items) ? page.items : [],
    ) ?? [];
  return { ...query, items };
}
