import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useCursorPages } from "@/features/finance/paging";

function Probe({
  load,
}: {
  load: (after?: string) => Promise<{
    data: Array<{ id: string }>;
    meta: { has_more: boolean; next_after: string | null };
  }>;
}) {
  const page = useCursorPages("probe", load);
  return (
    <div>
      <p>{page.rows.map((row) => row.id).join(",")}</p>
      {page.error ? <p role="alert">{page.error}</p> : null}
      <button
        disabled={!page.hasMore}
        onClick={() => void page.goNext()}
        type="button"
      >
        Next page
      </button>
    </div>
  );
}

describe("cursor paging safeguards", () => {
  it("stops when the next cursor repeats an earlier cursor", async () => {
    const load = vi
      .fn()
      .mockResolvedValueOnce({
        data: [{ id: "one" }],
        meta: { has_more: true, next_after: "cursor-a" },
      })
      .mockResolvedValueOnce({
        data: [{ id: "two" }],
        meta: { has_more: true, next_after: "cursor-a" },
      });
    render(<Probe load={load} />);
    expect(await screen.findByText("one")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("two")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This page cursor repeated",
    );
    expect(load).toHaveBeenCalledTimes(2);
  });
});
