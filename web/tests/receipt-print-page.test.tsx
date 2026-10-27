import { cleanup, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ReceiptPrintPage from "@/app/(print)/receipts/[id]/print/page";
import ReceiptTestPrintPage from "@/app/(print)/receipts/test-print/page";
import { requireTenantSession } from "@/lib/auth/session";
import type { CurrentUser } from "@/types/api";

const RECEIPT_ID = "11111111-1111-4111-8111-111111111111";

vi.mock("next/navigation", () => ({
  redirect: (path: string) => {
    throw new Error(`NEXT_REDIRECT:${path}`);
  },
}));

vi.mock("@/lib/auth/session", () => ({
  requireTenantSession: vi.fn(),
}));

vi.mock("@/features/finance/receipt-print-screen", () => ({
  FinalReceiptPrint: ({
    autoPrint,
    receiptId,
    timeZone,
  }: {
    autoPrint: boolean;
    receiptId: string;
    timeZone: string;
  }) => <p>{`final:${receiptId}:${timeZone}:${String(autoPrint)}`}</p>,
  TestReceiptPrint: ({
    autoPrint,
    currency,
    gymName,
  }: {
    autoPrint: boolean;
    currency: string;
    gymName: string;
  }) => <p>{`test:${gymName}:${currency}:${String(autoPrint)}`}</p>,
}));

const requireSession = vi.mocked(requireTenantSession);

function session(permissions: string[], features: string[]): CurrentUser {
  return {
    principal_type: "STAFF",
    permissions,
    features: features.map((code) => ({
      feature__code: code,
      limit_value: null,
    })),
    gym: {
      id: "gym-1",
      name: "Yangon Fitness",
      currency: "MMK",
      timezone: "Asia/Yangon",
    },
    gym_membership: { id: "membership-1", is_owner: false },
    user: { first_name: "Ada", email: "ada@example.test" },
  } as CurrentUser;
}

describe("receipt print pages", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.print = vi.fn();
    window.close = vi.fn();
  });

  it("requires receipt access and prints only when autoPrint is exactly 1", async () => {
    requireSession.mockResolvedValue(session(["payments.view"], ["finance"]));
    await expect(
      ReceiptPrintPage({
        params: Promise.resolve({ id: RECEIPT_ID }),
        searchParams: Promise.resolve({}),
      }),
    ).rejects.toThrow("NEXT_REDIRECT:");

    requireSession.mockResolvedValue(session(["receipts.view"], ["members"]));
    await expect(
      ReceiptPrintPage({
        params: Promise.resolve({ id: RECEIPT_ID }),
        searchParams: Promise.resolve({ autoPrint: "1" }),
      }),
    ).rejects.toThrow("NEXT_REDIRECT:");

    requireSession.mockResolvedValue(session(["receipts.view"], ["finance"]));
    render(
      await ReceiptPrintPage({
        params: Promise.resolve({ id: RECEIPT_ID.toUpperCase() }),
        searchParams: Promise.resolve({}),
      }),
    );
    expect(
      screen.getByText(`final:${RECEIPT_ID}:Asia/Yangon:false`),
    ).toBeInTheDocument();
    cleanup();

    render(
      await ReceiptPrintPage({
        params: Promise.resolve({ id: RECEIPT_ID }),
        searchParams: Promise.resolve({ autoPrint: "1" }),
      }),
    );
    expect(
      screen.getByText(`final:${RECEIPT_ID}:Asia/Yangon:true`),
    ).toBeInTheDocument();
    cleanup();

    render(
      await ReceiptPrintPage({
        params: Promise.resolve({ id: RECEIPT_ID }),
        searchParams: Promise.resolve({ autoPrint: "true" }),
      }),
    );
    expect(
      screen.getByText(`final:${RECEIPT_ID}:Asia/Yangon:false`),
    ).toBeInTheDocument();
    cleanup();

    render(
      await ReceiptPrintPage({
        params: Promise.resolve({ id: "not-a-receipt" }),
        searchParams: Promise.resolve({ autoPrint: "1" }),
      }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Receipt was not found.",
    );
    expect(screen.queryByText(/final:/)).not.toBeInTheDocument();
    expect(window.print).not.toHaveBeenCalled();
  });

  it("loads the test slip from the gym name already on the session", async () => {
    requireSession.mockResolvedValue(session(["settings.view"], ["finance"]));
    await expect(
      ReceiptTestPrintPage({ searchParams: Promise.resolve({}) }),
    ).rejects.toThrow("NEXT_REDIRECT:");

    requireSession.mockResolvedValue(session(["receipts.view"], ["finance"]));
    render(
      await ReceiptTestPrintPage({
        searchParams: Promise.resolve({ autoPrint: "1" }),
      }),
    );
    expect(
      screen.getByText("test:Yangon Fitness:MMK:true"),
    ).toBeInTheDocument();
  });
});
