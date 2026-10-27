import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GET as printContext } from "@/app/api/operations/receipts/print-context/route";
import { GET as printReceipt } from "@/app/api/operations/receipts/[id]/print/route";
import {
  getReceiptPrint,
  getReceiptPrintContext,
} from "@/features/finance/client";
import { OperationError } from "@/features/operations/client";
import { authenticatedDocumentGet } from "@/lib/api/server";
import { resetEnvForTests } from "@/lib/env";

const ID = "f52030c1-18af-4e18-a1ec-50997d892ef4";
const RECEIPT_DOWNLOAD_CSP =
  "default-src 'none'; sandbox; base-uri 'none'; form-action 'none'";

function env() {
  process.env.DJANGO_API_URL = "https://api.example.com";
  process.env.APP_ORIGINS = "https://app.example.com";
  process.env.ACCESS_EXPIRED_CODE = "ACCESS_TOKEN_EXPIRED";
  process.env.ALLOW_INSECURE_LOCALHOST_COOKIES = "false";
  process.env.BFF_SHARED_SECRET = "test-bff-shared-secret-32-chars-min";
  resetEnvForTests();
}

function authed(url: string) {
  return new NextRequest(url, { headers: { cookie: "gms_access=access" } });
}

describe("receipt print browser client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("reads print context and the final payload through the BFF", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () =>
        Response.json({ success: true, data: { printer_enabled: false } }),
      );

    await getReceiptPrintContext();
    await getReceiptPrint(ID.toUpperCase());

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/operations/receipts/print-context/",
      `/api/operations/receipts/${ID}/print/`,
    ]);
    for (const [, init] of fetchMock.mock.calls) {
      expect(init?.credentials).toBe("same-origin");
      expect(init?.cache).toBe("no-store");
      expect(new Headers(init?.headers).has("authorization")).toBe(false);
    }
  });

  it("rejects an invalid receipt id before calling the network", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    expect(() => getReceiptPrint("not-a-receipt")).toThrow(OperationError);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("receipt print BFF", () => {
  beforeEach(() => {
    env();
    vi.restoreAllMocks();
  });

  it("proxies the print payload and rejects queries or unknown ids", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () =>
        Response.json({ success: true, data: { receipt_number: "RCP-1" } }),
      );

    const printed = await printReceipt(
      authed(`https://app.example.com/api/operations/receipts/${ID}/print`),
      { params: Promise.resolve({ id: ID.toUpperCase() }) },
    );
    expect(printed.status).toBe(200);
    expect(printed.headers.get("Content-Security-Policy")).toBeNull();
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      `https://api.example.com/api/v1/receipts/${ID}/print/`,
    );

    fetchMock.mockClear();
    const queried = await printReceipt(
      authed(
        `https://app.example.com/api/operations/receipts/${ID}/print?autoPrint=1`,
      ),
      { params: Promise.resolve({ id: ID }) },
    );
    expect(queried.status).toBe(400);
    const missing = await printReceipt(
      authed("https://app.example.com/api/operations/receipts/not-an-id/print"),
      { params: Promise.resolve({ id: "not-an-id" }) },
    );
    expect(missing.status).toBe(404);
    const anonymous = await printReceipt(
      new NextRequest(
        `https://app.example.com/api/operations/receipts/${ID}/print`,
      ),
      { params: Promise.resolve({ id: ID }) },
    );
    expect(anonymous.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();

    const context = await printContext(
      authed("https://app.example.com/api/operations/receipts/print-context"),
    );
    expect(context.status).toBe(200);
    expect(String(fetchMock.mock.calls.at(-1)?.[0])).toBe(
      "https://api.example.com/api/v1/receipts/print-context/",
    );
    const contextQuery = await printContext(
      authed(
        "https://app.example.com/api/operations/receipts/print-context?debug=1",
      ),
    );
    expect(contextQuery.status).toBe(400);
  });

  it("keeps the downloaded receipt document sandboxed", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async () =>
        new Response("<p>Paid</p>", {
          status: 200,
          headers: {
            "content-type": "text/html; charset=utf-8",
            "content-disposition": 'attachment; filename="RCP-1.html"',
          },
        }),
    );
    const response = await authenticatedDocumentGet(
      authed("https://app.example.com/api/operations/receipts/download"),
      `/api/v1/receipts/${ID}/download/`,
    );
    expect(response.headers.get("Content-Security-Policy")).toBe(
      RECEIPT_DOWNLOAD_CSP,
    );
    expect(response.headers.get("Content-Security-Policy")).not.toMatch(
      /data:|unsafe-inline|script-src|img-src/,
    );
  });
});
