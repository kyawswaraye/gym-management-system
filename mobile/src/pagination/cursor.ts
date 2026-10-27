export type CursorMeta = {
  has_more: boolean;
  next_after: string | null;
};

export function cursorAfter(meta: CursorMeta | undefined): string | null {
  if (!meta?.has_more || !meta.next_after) {
    return null;
  }
  return meta.next_after;
}

export function appendCursorPage<T>(
  current: readonly T[],
  page: readonly T[],
): T[] {
  return [...current, ...page];
}
