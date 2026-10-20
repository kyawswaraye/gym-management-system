import { afterEach, describe, expect, it, vi } from "vitest";

import { downloadReportCsv } from "@/features/reports/download";

describe("report csv download", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("saves the document bytes without parsing csv rows", async () => {
    const blob = new Blob(["record_type,name\nROW,'=1+1\n"], {
      type: "text/csv",
    });
    const json = vi.fn(() => Promise.reject(new Error("parsed")));
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/auth/csrf") {
        return new Response(JSON.stringify({ data: { csrf_token: "csrf" } }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      return {
        ok: true,
        headers: new Headers({
          "content-type": "text/csv; charset=utf-8",
          "content-disposition":
            'attachment; filename="daily-YGN1-2026-10-01-2026-10-01.csv"',
        }),
        blob: () => Promise.resolve(blob),
        json,
      } as unknown as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("URL", {
      createObjectURL: () => "blob:report",
      revokeObjectURL: vi.fn(),
    });
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);

    await downloadReportCsv(
      "/api/operations/reports/daily/export/?date=2026-10-01",
    );

    expect(json).not.toHaveBeenCalled();
    expect(click).toHaveBeenCalledOnce();
    const exportCall = fetchMock.mock.calls.find((call) =>
      String(call[0]).includes("/reports/daily/export/"),
    );
    expect(exportCall?.[0]).toBe(
      "/api/operations/reports/daily/export/?date=2026-10-01",
    );
  });
});
