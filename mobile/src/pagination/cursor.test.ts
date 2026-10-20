import { appendCursorPage, cursorAfter } from "./cursor";

describe("cursor pagination", () => {
  it("follows next_after only when the server says there is another page", () => {
    expect(cursorAfter({ has_more: true, next_after: "cursor-2" })).toBe(
      "cursor-2",
    );
    expect(cursorAfter({ has_more: false, next_after: "cursor-2" })).toBeNull();
    expect(cursorAfter({ has_more: true, next_after: null })).toBeNull();
  });

  it("appends pages without rebuilding a cursor", () => {
    expect(appendCursorPage([{ id: "1" }], [{ id: "2" }])).toEqual([
      { id: "1" },
      { id: "2" },
    ]);
  });
});
