import { describe, expect, it, vi } from "vitest";

import { collectLookupPages, LOOKUP_PAGE_CAP } from "@/features/pt/lookup";

function row(id: string) {
  return { id };
}

describe("lookup page collection", () => {
  it("skips duplicate ids and follows a later cursor", async () => {
    const load = vi
      .fn()
      .mockResolvedValueOnce({
        data: [row("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")],
        meta: { has_more: true, next_after: "cursor-2" },
      })
      .mockResolvedValueOnce({
        data: [
          row("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
          row("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        ],
        meta: { has_more: false, next_after: null },
      });

    const result = await collectLookupPages(load);

    expect(load).toHaveBeenNthCalledWith(2, "cursor-2");
    expect(result).toEqual({
      items: [
        row("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        row("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
      ],
      bounded: false,
      error: "",
    });
  });

  it("stops when a cursor repeats and does not keep a partial list", async () => {
    const load = vi
      .fn()
      .mockResolvedValueOnce({
        data: [row("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")],
        meta: { has_more: true, next_after: "cursor-again" },
      })
      .mockResolvedValueOnce({
        data: [row("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")],
        meta: { has_more: true, next_after: "cursor-again" },
      });

    const result = await collectLookupPages(load);

    expect(load).toHaveBeenCalledTimes(2);
    expect(result.items).toEqual([]);
    expect(result.bounded).toBe(false);
    expect(result.error).toBe("This page cursor repeated. Refine the filters.");
  });

  it("stops at 20 pages and reports the bound", async () => {
    const load = vi.fn(async () => ({
      data: [row("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")],
      meta: { has_more: true, next_after: `cursor-${load.mock.calls.length}` },
    }));

    const result = await collectLookupPages(load);

    expect(load).toHaveBeenCalledTimes(LOOKUP_PAGE_CAP);
    expect(result.bounded).toBe(true);
    expect(result.items).toHaveLength(1);
    expect(result.error).toBe(
      "This list is larger than 2,000 rows. Refine the filters.",
    );
  });
});
