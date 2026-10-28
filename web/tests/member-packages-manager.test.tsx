import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MemberPackagesManager } from "@/features/pt/member-packages-manager";
import { listMembers } from "@/features/operations/client";
import {
  adjustPackageLedger,
  grantMemberPackage,
  listMemberPackages,
  listPackageLedger,
  listServicePackages,
} from "@/features/pt/client";

vi.mock("@/features/pt/client", () => ({
  listMemberPackages: vi.fn(),
  listPackageLedger: vi.fn(),
  listServicePackages: vi.fn(),
  grantMemberPackage: vi.fn(),
  adjustPackageLedger: vi.fn(),
}));

vi.mock("@/features/operations/client", () => ({
  listMembers: vi.fn(),
  OperationError: class OperationError extends Error {
    readonly code: string;
    constructor(
      message: string,
      _fields: Record<string, string> = {},
      code = "",
    ) {
      super(message);
      this.code = code;
    }
  },
}));

const MEMBER = "22222222-2222-4222-8222-222222222222";
const OTHER = "33333333-3333-4333-8333-333333333333";
const PACKAGE = "44444444-4444-4444-8444-444444444444";
const CLASS_PACKAGE = "55555555-5555-4555-8555-555555555555";
const GRANT = "66666666-6666-4666-8666-666666666666";
const OTHER_GRANT = "77777777-7777-4777-8777-777777777777";
const LEDGER = "88888888-8888-4888-8888-888888888888";
const ATTEND = "abababab-abab-4bab-8bab-abababababab";
const BOOKING = "99999999-9999-4999-8999-999999999999";
const CLASS_BOOKING = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const KEY = /^[A-Za-z0-9._:-]{16,128}$/;
const empty = { data: [], meta: { has_more: false, next_after: null } };

function page<T>(data: T[], next: string | null = null) {
  return {
    data,
    meta: { has_more: next !== null, next_after: next },
  } as never;
}

function deferred<T>() {
  let resolve: (value: T | PromiseLike<T>) => void = () => {};
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

const ptGrant = {
  id: GRANT,
  member_id: MEMBER,
  service_package_id: PACKAGE,
  package_type: "PT",
  package_code: "PT10",
  package_name: "Ten sessions",
  status: "ACTIVE",
  remaining_sessions: 9,
  granted_sessions: 10,
  valid_from: "2026-10-01",
  valid_until: "2027-01-01",
};

const classGrant = {
  id: OTHER_GRANT,
  member_id: OTHER,
  service_package_id: CLASS_PACKAGE,
  package_type: "CLASS",
  package_code: "YOGA8",
  package_name: "Yoga intro",
  status: "VOID",
  remaining_sessions: 0,
  granted_sessions: 8,
  valid_from: "2026-09-01",
  valid_until: "2026-09-08",
};

const grantEntry = {
  id: LEDGER,
  reason: "GRANT",
  session_delta: 10,
  occurred_at: "2026-10-01T00:00:00Z",
  pt_booking_id: null,
  class_booking_id: null,
};

const attendEntry = {
  id: ATTEND,
  reason: "ATTENDED",
  session_delta: -1,
  occurred_at: "2026-10-02T09:30:00Z",
  pt_booking_id: BOOKING,
  class_booking_id: CLASS_BOOKING,
};

function renderManager(canAdjust = true) {
  return render(
    <MemberPackagesManager canAdjust={canAdjust} today="2026-10-19" />,
  );
}

function compact(value: string | null): string {
  return (value ?? "").replace(/\s+/g, " ").trim();
}

async function selectGrant(name = /PT10 · Ten sessions · member/) {
  fireEvent.click(await screen.findByRole("button", { name }));
  const ledger = screen.getByRole("region", { name: "Session ledger" });
  await within(ledger).findByText("GRANT");
  return ledger;
}

describe("member package catalog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listMemberPackages).mockResolvedValue(empty);
    vi.mocked(listPackageLedger).mockResolvedValue(empty);
    vi.mocked(listServicePackages).mockResolvedValue(empty);
    vi.mocked(listMembers).mockResolvedValue(empty);
  });

  it("shows code, type, status, balance, validity, and compact ids", async () => {
    vi.mocked(listMemberPackages).mockResolvedValue(
      page([ptGrant, classGrant]),
    );
    renderManager();

    const catalog = await screen.findByRole("region", {
      name: "Granted packages",
    });
    const grants = within(catalog).getAllByRole("listitem");
    expect(grants).toHaveLength(2);

    expect(compact(grants[0]?.textContent ?? "")).toContain("PT10");
    expect(grants[0]).toHaveTextContent("Ten sessions");
    expect(grants[0]).toHaveTextContent("PT");
    expect(grants[0]).toHaveTextContent("ACTIVE");
    expect(compact(grants[0]?.textContent ?? "")).toContain(
      "ACTIVE · remaining 9 of 10",
    );
    expect(grants[0]).toHaveTextContent("01-10-2026 to 01-01-2027");
    expect(within(grants[0]!).getByTitle(MEMBER)).toHaveTextContent(
      MEMBER.slice(-6),
    );
    expect(within(grants[0]!).getByTitle(PACKAGE)).toHaveTextContent(
      PACKAGE.slice(-6),
    );
    expect(
      within(grants[0]!).getByRole("button", {
        name: /PT10 · Ten sessions · member/,
      }),
    ).toHaveAttribute("aria-pressed", "false");
    expect(within(grants[0]!).getByText("PT")).toHaveClass(
      "bg-amber-100",
      "text-amber-800",
    );
    expect(within(grants[0]!).getByText("ACTIVE")).toHaveClass(
      "bg-emerald-100",
      "text-emerald-800",
    );

    expect(compact(grants[1]?.textContent ?? "")).toContain(
      "VOID · remaining 0 of 8",
    );
    expect(grants[1]).toHaveTextContent("YOGA8");
    expect(grants[1]).toHaveTextContent("CLASS");
    expect(within(grants[1]!).getByText("CLASS")).toHaveClass(
      "bg-sky-100",
      "text-sky-800",
    );
    expect(within(grants[1]!).getByText("VOID")).toHaveClass(
      "bg-red-100",
      "text-red-800",
    );
    expect(
      screen.queryByRole("button", { name: "Correct ledger" }),
    ).not.toBeInTheDocument();
  });

  it("filters by status, type, and member, then clears them", async () => {
    vi.mocked(listMemberPackages).mockImplementation(async (query) => {
      if (query?.member_id === MEMBER && query.status === "ACTIVE") {
        return page([]);
      }
      if (query?.package_type === "CLASS") return page([classGrant]);
      if (query?.status === "ACTIVE") return page([ptGrant]);
      return page([ptGrant, classGrant]);
    });
    vi.mocked(listMembers).mockResolvedValue(
      page([
        {
          id: MEMBER,
          member_number: "M-1",
          first_name: "Grace",
          last_name: "Hopper",
        },
      ]),
    );
    renderManager();
    expect(await screen.findByText("Yoga intro")).toBeInTheDocument();
    await waitFor(() =>
      expect(listMemberPackages).toHaveBeenCalledWith({ limit: "25" }),
    );
    expect(
      screen.queryByRole("button", { name: "Clear filters" }),
    ).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: /PT10 · Ten sessions · member/ }),
    );
    expect(
      screen.queryByText("Choose a grant to read its ledger."),
    ).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Grant status"), {
      target: { value: "ACTIVE" },
    });
    await waitFor(() =>
      expect(listMemberPackages).toHaveBeenLastCalledWith({
        limit: "25",
        status: "ACTIVE",
      }),
    );
    expect(
      screen.getByText("Choose a grant to read its ledger."),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByText("Yoga intro")).not.toBeInTheDocument(),
    );

    fireEvent.change(screen.getByLabelText("Package type"), {
      target: { value: "CLASS" },
    });
    await waitFor(() =>
      expect(listMemberPackages).toHaveBeenLastCalledWith({
        limit: "25",
        status: "ACTIVE",
        package_type: "CLASS",
      }),
    );

    fireEvent.change(screen.getByLabelText("Grant status"), {
      target: { value: "" },
    });
    fireEvent.change(screen.getByLabelText("Package type"), {
      target: { value: "" },
    });
    fireEvent.change(screen.getByLabelText("Filter grants by member"), {
      target: { value: "Grace" },
    });
    fireEvent.click(
      await screen.findByRole("button", { name: "Grace Hopper (M-1)" }),
    );
    await waitFor(() =>
      expect(listMemberPackages).toHaveBeenLastCalledWith({
        limit: "25",
        member_id: MEMBER,
      }),
    );
    expect(
      screen.getByText(/Selected member: Grace Hopper/),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Grant status"), {
      target: { value: "ACTIVE" },
    });
    await waitFor(() =>
      expect(listMemberPackages).toHaveBeenLastCalledWith({
        limit: "25",
        status: "ACTIVE",
        member_id: MEMBER,
      }),
    );
    expect(await screen.findByText("No grants match.")).toBeInTheDocument();
    expect(
      screen.getByText("Try changing or clearing the current filters."),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() =>
      expect(listMemberPackages).toHaveBeenLastCalledWith({ limit: "25" }),
    );
    expect(
      vi.mocked(listMemberPackages).mock.calls.at(-1)?.[0],
    ).not.toHaveProperty("after");
    expect(await screen.findByText("Yoga intro")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Clear filters" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Selected member:/)).not.toBeInTheDocument();
  });

  it("shows an empty catalog, then a load failure with retry", async () => {
    const readOnly = renderManager(false);
    expect(await screen.findByText("No grants match.")).toBeInTheDocument();
    expect(
      screen.getByText("Grants will appear here when a package is granted."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Grant package" }),
    ).not.toBeInTheDocument();
    readOnly.unmount();

    vi.mocked(listMemberPackages).mockRejectedValueOnce(
      new Error("The grants could not be loaded."),
    );
    renderManager();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The grants could not be loaded.",
    );
    expect(screen.queryByText("No grants match.")).not.toBeInTheDocument();
    vi.mocked(listMemberPackages).mockResolvedValue(page([ptGrant]));
    fireEvent.click(
      screen.getByRole("button", { name: "Retry loading grants" }),
    );
    expect(await screen.findByText("Ten sessions")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a loading state before the first page arrives", async () => {
    const pendingList = deferred<never>();
    vi.mocked(listMemberPackages).mockReturnValue(pendingList.promise as never);
    renderManager();
    expect(screen.getByRole("status")).toHaveTextContent("Loading grants.");
    expect(screen.queryByText("No grants match.")).not.toBeInTheDocument();
    await act(async () => {
      pendingList.resolve(page([ptGrant]));
      await pendingList.promise;
    });
    expect(await screen.findByText("Ten sessions")).toBeInTheDocument();
  });

  it("pages the catalog from the card footer", async () => {
    vi.mocked(listMemberPackages).mockImplementation(async (query) =>
      query?.after ? page([classGrant]) : page([ptGrant], "grant-2"),
    );
    renderManager();
    const catalog = await screen.findByRole("region", {
      name: "Granted packages",
    });
    expect(
      await within(catalog).findByText("Ten sessions"),
    ).toBeInTheDocument();
    const list = within(catalog).getByRole("list", {
      name: "Granted packages",
    });
    const paging = within(catalog).getByRole("navigation", {
      name: "Pagination",
    });
    expect(
      list.compareDocumentPosition(paging) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    fireEvent.click(within(paging).getByRole("button", { name: "Next page" }));
    expect(await within(catalog).findByText("Yoga intro")).toBeInTheDocument();
    await waitFor(() =>
      expect(listMemberPackages).toHaveBeenLastCalledWith({
        limit: "25",
        after: "grant-2",
      }),
    );
    fireEvent.click(
      within(paging).getByRole("button", { name: "Previous page" }),
    );
    expect(
      await within(catalog).findByText("Ten sessions"),
    ).toBeInTheDocument();
  });
});

describe("session ledger", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listMemberPackages).mockResolvedValue(
      page([ptGrant, classGrant]),
    );
    vi.mocked(listPackageLedger).mockResolvedValue(
      page([grantEntry, attendEntry]),
    );
    vi.mocked(listServicePackages).mockResolvedValue(empty);
    vi.mocked(listMembers).mockResolvedValue(empty);
  });

  it("selects a grant and shows reason, signed delta, time, and booking ids", async () => {
    renderManager(false);
    expect(
      screen.getByText("Choose a grant to read its ledger."),
    ).toBeInTheDocument();
    const ledger = await selectGrant();
    const catalog = screen.getByRole("region", { name: "Granted packages" });
    const selected = within(catalog).getByRole("button", {
      name: /PT10 · Ten sessions · member/,
    });
    expect(selected).toHaveAttribute("aria-pressed", "true");
    expect(within(selected).getByText("Selected")).toBeInTheDocument();
    expect(ledger).toHaveTextContent("PT10");
    expect(ledger).toHaveTextContent("Ten sessions");
    expect(within(ledger).getByText("Viewing")).toBeInTheDocument();
    expect(compact(ledger.textContent ?? "")).toContain("GRANT · 10");
    expect(compact(ledger.textContent ?? "")).toContain("ATTENDED · -1");
    expect(ledger).toHaveTextContent("+10");
    expect(ledger).toHaveTextContent("-1");
    expect(ledger).toHaveTextContent("01-10-2026 00:00");
    expect(ledger).toHaveTextContent("02-10-2026 09:30");
    expect(within(ledger).getByText("+10")).toHaveClass("text-emerald-700");
    expect(within(ledger).getByText("-1")).toHaveClass("text-red-700");
    expect(ledger).toHaveTextContent("PT booking");
    expect(ledger).toHaveTextContent("class booking");
    expect(within(ledger).getByTitle(BOOKING)).toHaveTextContent(
      BOOKING.slice(-6),
    );
    expect(within(ledger).getByTitle(CLASS_BOOKING)).toHaveTextContent(
      CLASS_BOOKING.slice(-6),
    );
    expect(
      screen.queryByRole("button", { name: "Correct ledger" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Grant package" }),
    ).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: /YOGA8 · Yoga intro · member/ }),
    );
    expect(
      screen.getByRole("button", { name: /PT10 · Ten sessions · member/ }),
    ).toHaveAttribute("aria-pressed", "false");
    expect(
      screen.getByRole("button", { name: /YOGA8 · Yoga intro · member/ }),
    ).toHaveAttribute("aria-pressed", "true");
    await waitFor(() =>
      expect(listPackageLedger).toHaveBeenLastCalledWith(OTHER_GRANT, {
        limit: "25",
      }),
    );
  });

  it("pages the ledger and retries a failed load", async () => {
    vi.mocked(listPackageLedger).mockImplementation(async (_id, query) =>
      query?.after ? page([attendEntry]) : page([grantEntry], "ledger-2"),
    );
    renderManager();
    const ledger = await selectGrant();
    expect(compact(ledger.textContent ?? "")).toContain("GRANT · 10");
    const paging = within(ledger).getByRole("navigation", {
      name: "Pagination",
    });
    fireEvent.click(within(paging).getByRole("button", { name: "Next page" }));
    expect(await within(ledger).findByText("ATTENDED")).toBeInTheDocument();
    await waitFor(() =>
      expect(listPackageLedger).toHaveBeenLastCalledWith(GRANT, {
        limit: "25",
        after: "ledger-2",
      }),
    );

    vi.mocked(listPackageLedger).mockRejectedValueOnce(
      new Error("The ledger could not be loaded."),
    );
    fireEvent.click(
      within(ledger).getByRole("button", { name: "Previous page" }),
    );
    expect(await within(ledger).findByRole("alert")).toHaveTextContent(
      "The ledger could not be loaded.",
    );
    vi.mocked(listPackageLedger).mockResolvedValue(page([grantEntry]));
    fireEvent.click(
      within(ledger).getByRole("button", { name: "Retry loading the ledger" }),
    );
    expect(await within(ledger).findByText("GRANT")).toBeInTheDocument();
    expect(within(ledger).queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("grant package dialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listMemberPackages).mockResolvedValue(page([ptGrant]));
    vi.mocked(listPackageLedger).mockResolvedValue(empty);
    vi.mocked(listServicePackages).mockResolvedValue(
      page([{ id: PACKAGE, code: "PT10", name: "Ten", type: "PT" }]),
    );
    vi.mocked(listMembers).mockResolvedValue(
      page([
        {
          id: MEMBER,
          member_number: "M-1",
          first_name: "Grace",
          last_name: "Hopper",
        },
      ]),
    );
  });

  async function openGrantDialog() {
    fireEvent.click(screen.getByRole("button", { name: "Grant package" }));
    return screen.findByRole("dialog", { name: "Grant package" });
  }

  async function fillGrant(dialog: HTMLElement, reason = "Courtesy") {
    fireEvent.change(within(dialog).getByLabelText("Search active members"), {
      target: { value: "Grace" },
    });
    fireEvent.click(
      await within(dialog).findByRole("button", { name: "Grace Hopper (M-1)" }),
    );
    await within(dialog).findByRole("option", { name: /PT · PT10/ });
    fireEvent.change(within(dialog).getByLabelText("Package"), {
      target: { value: PACKAGE },
    });
    fireEvent.change(within(dialog).getByLabelText("Reason"), {
      target: { value: reason },
    });
  }

  it("opens a labelled modal and closes from cancel, close, escape, and the backdrop", async () => {
    renderManager();
    await screen.findByText("Ten sessions");
    expect(
      screen.queryByRole("dialog", { name: "Grant package" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Grant package" }),
    ).toHaveAttribute("aria-expanded", "false");

    const dialog = await openGrantDialog();
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAttribute("aria-labelledby", "grant-title");
    expect(dialog).toHaveAttribute(
      "aria-describedby",
      "grant-package-description",
    );
    expect(dialog).toHaveAttribute("id", "grant-package-dialog");
    expect(
      within(dialog).getByRole("heading", { name: "Grant package" }),
    ).toHaveAttribute("id", "grant-title");
    expect(dialog).toHaveClass(
      "h-full",
      "max-h-full",
      "overflow-y-auto",
      "overscroll-contain",
      "sm:h-auto",
      "sm:max-w-2xl",
    );
    expect(dialog.className).toContain("sm:max-h-[min(90dvh,48rem)]");
    expect(dialog.querySelector(".sticky.top-0")).not.toBeNull();
    expect(dialog.querySelector(".sticky.bottom-0")).not.toBeNull();
    expect(dialog).toHaveTextContent(/sale approval/);
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeEnabled();
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveClass(
      "w-full",
      "sm:w-auto",
    );
    expect(
      within(dialog).getByRole("button", { name: "Save grant" }),
    ).toHaveClass("button-create", "w-full", "sm:w-auto");
    const backdrop = dialog.parentElement;
    if (!backdrop) throw new Error("Missing grant dialog backdrop.");
    expect(backdrop).toHaveAttribute("role", "presentation");
    expect(backdrop).toHaveClass(
      "fixed",
      "inset-0",
      "overflow-hidden",
      "sm:place-items-center",
    );
    expect(
      screen.getByRole("button", { name: "Grant package", hidden: true }),
    ).toHaveAttribute("aria-expanded", "true");
    expect(
      screen.getByRole("button", { name: "Grant package", hidden: true }),
    ).toHaveAttribute("aria-controls", "grant-package-dialog");

    fireEvent.mouseDown(within(dialog).getByLabelText("Reason"));
    expect(screen.getByRole("dialog", { name: "Grant package" })).toBe(dialog);

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(
      screen.queryByRole("dialog", { name: "Grant package" }),
    ).not.toBeInTheDocument();

    const closed = await openGrantDialog();
    fireEvent.click(within(closed).getByRole("button", { name: "Close" }));
    expect(
      screen.queryByRole("dialog", { name: "Grant package" }),
    ).not.toBeInTheDocument();

    const escaped = await openGrantDialog();
    fireEvent.keyDown(escaped, { key: "Escape" });
    expect(
      screen.queryByRole("dialog", { name: "Grant package" }),
    ).not.toBeInTheDocument();

    const dismissed = await openGrantDialog();
    const dismissedBackdrop = dismissed.parentElement;
    if (!dismissedBackdrop) throw new Error("Missing grant dialog backdrop.");
    fireEvent.mouseDown(dismissedBackdrop);
    expect(
      screen.queryByRole("dialog", { name: "Grant package" }),
    ).not.toBeInTheDocument();
  });

  it("keeps validation and request failures inside the dialog", async () => {
    renderManager();
    await screen.findByText("Ten sessions");
    const dialog = await openGrantDialog();
    fireEvent.click(within(dialog).getByRole("button", { name: "Save grant" }));
    const memberError = within(dialog).getByRole("alert");
    expect(memberError).toHaveTextContent("Select an active member.");
    expect(dialog).toContainElement(memberError);
    expect(screen.getAllByRole("alert")).toEqual([memberError]);
    expect(grantMemberPackage).not.toHaveBeenCalled();

    await fillGrant(dialog, "   ");
    fireEvent.click(within(dialog).getByRole("button", { name: "Save grant" }));
    const reasonError = within(dialog).getByRole("alert");
    expect(reasonError).toHaveTextContent("Enter a reason.");
    expect(grantMemberPackage).not.toHaveBeenCalled();

    fireEvent.change(within(dialog).getByLabelText("Reason"), {
      target: { value: "Courtesy" },
    });
    vi.mocked(grantMemberPackage).mockRejectedValue(
      new Error("The member cannot receive this package."),
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Save grant" }));
    const failure = await within(dialog).findByRole("alert");
    expect(failure).toHaveTextContent(
      "The member cannot receive this package.",
    );
    expect(dialog).toContainElement(failure);
    expect(screen.getAllByRole("alert")).toEqual([failure]);
    expect(screen.queryByText("Package granted.")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Grant package" })).toBe(dialog);

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    const reopened = await openGrantDialog();
    expect(within(reopened).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("reuses an idempotency key until the grant succeeds", async () => {
    vi.mocked(grantMemberPackage).mockRejectedValue(new Error("Try again."));
    renderManager();
    await screen.findByText("Ten sessions");
    const dialog = await openGrantDialog();
    await fillGrant(dialog);
    fireEvent.click(within(dialog).getByRole("button", { name: "Save grant" }));
    await within(dialog).findByRole("alert");
    const firstKey = vi.mocked(grantMemberPackage).mock.calls[0]?.[1];
    expect(firstKey).toMatch(KEY);
    expect(grantMemberPackage).toHaveBeenCalledWith(
      expect.objectContaining({
        member_id: MEMBER,
        service_package_id: PACKAGE,
        business_date: "2026-10-19",
        complimentary: true,
        reason: "Courtesy",
      }),
      firstKey,
    );

    fireEvent.click(within(dialog).getByRole("button", { name: "Save grant" }));
    await waitFor(() => expect(grantMemberPackage).toHaveBeenCalledTimes(2));
    expect(vi.mocked(grantMemberPackage).mock.calls[1]?.[1]).toBe(firstKey);

    fireEvent.change(within(dialog).getByLabelText("Reason"), {
      target: { value: "Second courtesy" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save grant" }));
    await waitFor(() => expect(grantMemberPackage).toHaveBeenCalledTimes(3));
    const changedKey = vi.mocked(grantMemberPackage).mock.calls[2]?.[1];
    expect(changedKey).toMatch(KEY);
    expect(changedKey).not.toBe(firstKey);

    vi.mocked(grantMemberPackage).mockResolvedValue({} as never);
    fireEvent.change(within(dialog).getByLabelText("Reason"), {
      target: { value: "Courtesy" },
    });
    const reloads = vi.mocked(listMemberPackages).mock.calls.length;
    fireEvent.click(within(dialog).getByRole("button", { name: "Save grant" }));
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Grant package" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByText("Package granted.")).toHaveAttribute(
      "role",
      "status",
    );
    await waitFor(() =>
      expect(vi.mocked(listMemberPackages).mock.calls.length).toBeGreaterThan(
        reloads,
      ),
    );
    const successKey = vi.mocked(grantMemberPackage).mock.calls[3]?.[1];
    expect(successKey).not.toBe(changedKey);

    const again = await openGrantDialog();
    await fillGrant(again);
    vi.mocked(grantMemberPackage).mockResolvedValue({} as never);
    fireEvent.click(within(again).getByRole("button", { name: "Save grant" }));
    await waitFor(() => expect(grantMemberPackage).toHaveBeenCalledTimes(5));
    expect(vi.mocked(grantMemberPackage).mock.calls[4]?.[1]).not.toBe(
      successKey,
    );
  });

  it("ignores dismissals while the grant is saving", async () => {
    const pendingGrant = deferred<never>();
    vi.mocked(grantMemberPackage).mockImplementation(
      () => pendingGrant.promise as never,
    );
    renderManager();
    await screen.findByText("Ten sessions");
    const dialog = await openGrantDialog();
    await fillGrant(dialog);
    fireEvent.click(within(dialog).getByRole("button", { name: "Save grant" }));
    await waitFor(() => expect(grantMemberPackage).toHaveBeenCalledTimes(1));
    expect(dialog).toHaveAttribute("aria-busy", "true");
    expect(
      within(dialog).getByRole("button", { name: "Close" }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "Cancel" }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "Save grant" }),
    ).toBeDisabled();
    expect(within(dialog).getByLabelText("Package")).toBeDisabled();

    fireEvent.keyDown(dialog, { key: "Escape" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    fireEvent.mouseDown(dialog.parentElement ?? dialog);
    fireEvent.click(within(dialog).getByRole("button", { name: "Save grant" }));
    expect(screen.getByRole("dialog", { name: "Grant package" })).toBe(dialog);
    expect(grantMemberPackage).toHaveBeenCalledTimes(1);

    await act(async () => {
      pendingGrant.resolve({} as never);
      await pendingGrant.promise;
    });
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Grant package" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByText("Package granted.")).toBeInTheDocument();
  });
});

describe("correct ledger dialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listMemberPackages).mockResolvedValue(page([ptGrant]));
    vi.mocked(listPackageLedger).mockResolvedValue(
      page([grantEntry, attendEntry]),
    );
    vi.mocked(listServicePackages).mockResolvedValue(empty);
    vi.mocked(listMembers).mockResolvedValue(empty);
  });

  async function openCorrectDialog() {
    await selectGrant();
    fireEvent.click(screen.getByRole("button", { name: "Correct ledger" }));
    return screen.findByRole("dialog", { name: "Correct ledger" });
  }

  it("identifies the package and excludes grant entries", async () => {
    renderManager();
    const dialog = await openCorrectDialog();
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAttribute("aria-labelledby", "correct-title");
    expect(dialog).toHaveAttribute("id", "correct-ledger-dialog");
    expect(
      within(dialog).getByRole("heading", { name: "Correct ledger" }),
    ).toHaveAttribute("id", "correct-title");
    expect(dialog).toHaveClass("h-full", "sm:h-auto", "sm:max-w-2xl");
    expect(dialog.className).toContain("sm:max-h-[min(90dvh,48rem)]");
    expect(within(dialog).getByText("Selected package")).toBeInTheDocument();
    expect(within(dialog).getByText("PT10")).toBeInTheDocument();
    expect(within(dialog).getByText("Ten sessions")).toBeInTheDocument();
    expect(within(dialog).getByTitle(MEMBER)).toHaveTextContent(
      MEMBER.slice(-6),
    );
    expect(compact(dialog.textContent ?? "")).toContain(
      "ACTIVE · remaining 9 of 10",
    );
    expect(
      within(dialog).queryByRole("option", { name: /^GRANT/ }),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).getByRole("option", { name: /ATTENDED · -1/ }),
    ).toBeInTheDocument();
    expect(dialog).toHaveTextContent(/except grants/);

    fireEvent.change(within(dialog).getByLabelText("Ledger entry to reverse"), {
      target: { value: ATTEND },
    });
    expect(within(dialog).getByText("Reversal entry")).toBeInTheDocument();
    expect(compact(dialog.textContent ?? "")).toContain("ATTENDED · -1");
    expect(within(dialog).getByTitle(ATTEND)).toHaveTextContent(
      ATTEND.slice(-6),
    );
    expect(within(dialog).getByTitle(BOOKING)).toHaveTextContent(
      BOOKING.slice(-6),
    );
    expect(dialog).toHaveTextContent("02-10-2026 09:30");

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(
      screen.queryByRole("dialog", { name: "Correct ledger" }),
    ).not.toBeInTheDocument();
    const reopened = await openCorrectDialog();
    fireEvent.keyDown(reopened, { key: "Escape" });
    expect(
      screen.queryByRole("dialog", { name: "Correct ledger" }),
    ).not.toBeInTheDocument();
  });

  it("does not submit a page that only contains a grant", async () => {
    vi.mocked(listPackageLedger).mockResolvedValue(page([grantEntry]));
    renderManager();
    const dialog = await openCorrectDialog();
    expect(
      within(dialog).queryByRole("option", { name: /^GRANT/ }),
    ).not.toBeInTheDocument();
    expect(dialog).toHaveTextContent("No reversible entries are on this page.");
    expect(dialog).toHaveTextContent("Grant entries cannot be corrected.");
    expect(
      within(dialog).getByRole("button", { name: "Save correction" }),
    ).toBeDisabled();
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save correction" }),
    );
    expect(adjustPackageLedger).not.toHaveBeenCalled();
  });

  it("keeps correction errors in the dialog and reuses the idempotency key", async () => {
    vi.mocked(adjustPackageLedger).mockRejectedValue(
      new Error("The entry was already reversed."),
    );
    renderManager();
    const dialog = await openCorrectDialog();
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save correction" }),
    );
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Choose a ledger entry to reverse.",
    );
    expect(adjustPackageLedger).not.toHaveBeenCalled();
    expect(screen.getAllByRole("alert")).toHaveLength(1);

    fireEvent.change(within(dialog).getByLabelText("Ledger entry to reverse"), {
      target: { value: ATTEND },
    });
    fireEvent.change(within(dialog).getByLabelText("Correction reason"), {
      target: { value: "Wrong attendance" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save correction" }),
    );
    const failure = await within(dialog).findByRole("alert");
    expect(failure).toHaveTextContent("The entry was already reversed.");
    expect(dialog).toContainElement(failure);
    expect(screen.queryByText("Ledger corrected.")).not.toBeInTheDocument();
    const firstKey = vi.mocked(adjustPackageLedger).mock.calls[0]?.[2];
    expect(adjustPackageLedger).toHaveBeenCalledWith(
      GRANT,
      { reverses_entry_id: ATTEND, reason: "Wrong attendance" },
      firstKey,
    );
    expect(firstKey).toMatch(KEY);

    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save correction" }),
    );
    await waitFor(() => expect(adjustPackageLedger).toHaveBeenCalledTimes(2));
    expect(vi.mocked(adjustPackageLedger).mock.calls[1]?.[2]).toBe(firstKey);

    vi.mocked(adjustPackageLedger).mockResolvedValue({} as never);
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save correction" }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Correct ledger" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByText("Ledger corrected.")).toHaveAttribute(
      "role",
      "status",
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Correct ledger" }));
    const again = await screen.findByRole("dialog", { name: "Correct ledger" });
    fireEvent.change(within(again).getByLabelText("Ledger entry to reverse"), {
      target: { value: ATTEND },
    });
    fireEvent.change(within(again).getByLabelText("Correction reason"), {
      target: { value: "Wrong attendance" },
    });
    fireEvent.click(
      within(again).getByRole("button", { name: "Save correction" }),
    );
    await waitFor(() => expect(adjustPackageLedger).toHaveBeenCalledTimes(4));
    expect(vi.mocked(adjustPackageLedger).mock.calls[3]?.[2]).not.toBe(
      firstKey,
    );
  });

  it("ignores dismissals while the correction is saving", async () => {
    const pendingCorrection = deferred<never>();
    vi.mocked(adjustPackageLedger).mockImplementation(
      () => pendingCorrection.promise as never,
    );
    renderManager();
    const dialog = await openCorrectDialog();
    fireEvent.change(within(dialog).getByLabelText("Ledger entry to reverse"), {
      target: { value: ATTEND },
    });
    fireEvent.change(within(dialog).getByLabelText("Correction reason"), {
      target: { value: "Wrong attendance" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save correction" }),
    );
    await waitFor(() => expect(adjustPackageLedger).toHaveBeenCalledTimes(1));
    expect(dialog).toHaveAttribute("aria-busy", "true");
    expect(
      within(dialog).getByRole("button", { name: "Close" }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "Cancel" }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "Save correction" }),
    ).toBeDisabled();

    fireEvent.keyDown(dialog, { key: "Escape" });
    fireEvent.mouseDown(dialog.parentElement ?? dialog);
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.getByRole("dialog", { name: "Correct ledger" })).toBe(dialog);
    expect(adjustPackageLedger).toHaveBeenCalledTimes(1);

    await act(async () => {
      pendingCorrection.resolve({} as never);
      await pendingCorrection.promise;
    });
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Correct ledger" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByText("Ledger corrected.")).toBeInTheDocument();
  });
});
