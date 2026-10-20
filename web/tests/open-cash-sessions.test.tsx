import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { listCashSessions } from "@/features/finance/client";
import { useOpenCashSessions } from "@/features/finance/cash-session-field";
import { LOOKUP_PAGE_CAP } from "@/features/finance/paging";
import type { CashSession } from "@/types/api";

vi.mock("@/features/finance/client", () => ({
  listCashSessions: vi.fn(),
}));

function session(id: string, register = "FRONT"): CashSession {
  return {
    id,
    status: "OPEN",
    currency: "MMK",
    register_code: register,
    business_date: "2026-09-30",
  } as CashSession;
}

function page(data: CashSession[], next: string | null = null) {
  return {
    data,
    meta: { has_more: next !== null, next_after: next },
  };
}

describe("open cash session collection", () => {
  it("keeps a later page, skips duplicate ids, and reloads for a new date", async () => {
    const first = session("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "FRONT");
    const second = session("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "BACK");
    vi.mocked(listCashSessions).mockImplementation(async (query) => {
      if (query?.business_date === "2026-10-02") return page([second]);
      if (query?.after === "sess-2") return page([first, second]);
      return page([first], "sess-2");
    });
    const hook = renderHook(
      ({ date }: { date: string }) => useOpenCashSessions(date),
      { initialProps: { date: "2026-09-30" } },
    );
    await waitFor(() => expect(hook.result.current.pending).toBe(false));
    expect(hook.result.current.sessions.map((row) => row.id)).toEqual([
      first.id,
      second.id,
    ]);
    expect(hook.result.current.bounded).toBe(false);
    expect(hook.result.current.error).toBe("");
    expect(listCashSessions).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({
        after: "sess-2",
        business_date: "2026-09-30",
        limit: "100",
        status: "OPEN",
      }),
    );

    hook.rerender({ date: "2026-10-02" });
    await waitFor(() =>
      expect(
        hook.result.current.sessions.map((row) => row.register_code),
      ).toEqual(["BACK"]),
    );
    expect(listCashSessions).toHaveBeenCalledWith(
      expect.objectContaining({ business_date: "2026-10-02", status: "OPEN" }),
    );
    hook.rerender({ date: "" });
    await waitFor(() => expect(hook.result.current.pending).toBe(false));
    expect(hook.result.current.sessions).toEqual([]);
  });

  it("drops the list when a cursor repeats", async () => {
    vi.mocked(listCashSessions).mockImplementation(async (query) =>
      query?.after
        ? page([session("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")], "again")
        : page([session("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")], "again"),
    );
    const hook = renderHook(() => useOpenCashSessions("2026-09-30"));
    await waitFor(() => expect(hook.result.current.pending).toBe(false));
    expect(hook.result.current.sessions).toEqual([]);
    expect(hook.result.current.bounded).toBe(false);
    expect(hook.result.current.error).toBe(
      "This page cursor repeated. Refine the filters.",
    );
  });

  it("stops at 20 pages and fails closed", async () => {
    vi.mocked(listCashSessions).mockImplementation(async (query) => {
      const previous = query?.after?.startsWith("cursor-")
        ? Number(query.after.slice("cursor-".length))
        : 0;
      const next = previous + 1;
      return page(
        [session(`cccccccc-cccc-4ccc-8ccc-${String(next).padStart(12, "0")}`)],
        `cursor-${next}`,
      );
    });
    const hook = renderHook(() => useOpenCashSessions("2026-09-30"));
    await waitFor(() => expect(hook.result.current.pending).toBe(false));
    expect(listCashSessions).toHaveBeenCalledWith(
      expect.objectContaining({ after: `cursor-${LOOKUP_PAGE_CAP - 1}` }),
    );
    expect(listCashSessions).not.toHaveBeenCalledWith(
      expect.objectContaining({ after: `cursor-${LOOKUP_PAGE_CAP}` }),
    );
    expect(hook.result.current.bounded).toBe(true);
    expect(hook.result.current.hasMore).toBe(true);
    expect(hook.result.current.sessions).toEqual([]);
    expect(hook.result.current.error).toBe(
      "This list is larger than 2,000 rows. Refine the filters.",
    );
  });
});
