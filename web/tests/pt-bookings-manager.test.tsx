import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BookingsManager } from "@/features/pt/bookings-manager";
import {
  attendPtBooking,
  cancelPtBooking,
  createPtBooking,
  listMemberPackages,
  listPtBookings,
  listTrainers,
  noShowPtBooking,
} from "@/features/pt/client";
import { listMembers } from "@/features/operations/client";

vi.mock("@/features/pt/client", () => ({
  listPtBookings: vi.fn(),
  createPtBooking: vi.fn(),
  attendPtBooking: vi.fn(),
  noShowPtBooking: vi.fn(),
  cancelPtBooking: vi.fn(),
  listTrainers: vi.fn(),
  listMemberPackages: vi.fn(),
}));

vi.mock("@/features/operations/client", () => ({
  listMembers: vi.fn(),
  OperationError: class OperationError extends Error {},
}));

const TRAINER = "11111111-1111-4111-8111-111111111111";
const MEMBER = "22222222-2222-4222-8222-222222222222";
const OTHER = "33333333-3333-4333-8333-333333333333";
const BOOKING = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const SECOND = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const GRANT = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const OTHER_GRANT = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
const KEY = /^[A-Za-z0-9._:-]{16,128}$/;

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

function booking(
  extra: Partial<{
    id: string;
    member_id: string;
    member_name: string;
    status: string;
    unmetered: boolean;
    unmetered_reason: string;
    reason: string;
    member_service_package_id: string | null;
  }> = {},
) {
  return {
    id: BOOKING,
    trainer_staff_profile_id: TRAINER,
    trainer_display_name: "Ada Trainer",
    member_id: MEMBER,
    member_name: "MEM-1 · Ada Member",
    member_service_package_id: GRANT,
    starts_at: "2026-10-19T09:00:00Z",
    ends_at: "2026-10-19T10:00:00Z",
    unmetered: false,
    unmetered_reason: "",
    status: "BOOKED",
    reason: "",
    ...extra,
  };
}

function renderManager({
  canManage = true,
  canUnmetered = false,
  timeZone = "UTC",
}: {
  canManage?: boolean;
  canUnmetered?: boolean;
  timeZone?: string;
} = {}) {
  return render(
    <BookingsManager
      canManage={canManage}
      canUnmetered={canUnmetered}
      timeZone={timeZone}
      today="2026-10-19"
    />,
  );
}

async function openBookDialog() {
  fireEvent.click(screen.getByRole("button", { name: "Book session" }));
  return screen.findByRole("dialog", { name: "Book session" });
}

async function chooseGrace(dialog: HTMLElement) {
  fireEvent.change(within(dialog).getByLabelText("Search active members"), {
    target: { value: "Grace" },
  });
  fireEvent.click(
    await within(dialog).findByRole("button", { name: "Grace Hopper (M-1)" }),
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listPtBookings).mockResolvedValue(page([]));
  vi.mocked(listTrainers).mockResolvedValue(
    page([{ id: TRAINER, staff_code: "TR-1", display_name: "Ada" }]),
  );
  vi.mocked(listMemberPackages).mockResolvedValue(
    page([
      {
        id: GRANT,
        package_code: "PT10",
        package_name: "Ten",
        remaining_sessions: 4,
        status: "ACTIVE",
      },
    ]),
  );
  vi.mocked(listMembers).mockResolvedValue(
    page([
      {
        id: MEMBER,
        member_number: "M-1",
        first_name: "Grace",
        last_name: "Hopper",
      },
      {
        id: OTHER,
        member_number: "M-2",
        first_name: "Ada",
        last_name: "Lovelace",
      },
    ]),
  );
});

describe("PT bookings calendar", () => {
  it("loads the default gym-local week and shows member and trainer names", async () => {
    vi.mocked(listPtBookings).mockResolvedValue(page([booking()]));
    renderManager();
    await waitFor(() =>
      expect(listPtBookings).toHaveBeenCalledWith({
        limit: "25",
        view: "calendar",
        date_from: "2026-10-19",
        date_to: "2026-10-25",
      }),
    );
    expect(await screen.findByText("MEM-1 · Ada Member")).toBeInTheDocument();
    expect(screen.getByText("Ada Trainer")).toBeInTheDocument();
    expect(screen.getByTitle(GRANT)).toHaveTextContent(GRANT.slice(-6));
    expect(screen.getByText("BOOKED")).toBeInTheDocument();
    expect(
      screen.getByText(/Calendar range is at most 42 gym-local days/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Showing 2026-10-19 to 2026-10-25/),
    ).toBeInTheDocument();
  });

  it("shows a loading state, then the empty calendar", async () => {
    const pendingList = deferred<never>();
    vi.mocked(listPtBookings).mockReturnValue(pendingList.promise as never);
    renderManager();
    expect(await screen.findByText("Loading bookings.")).toBeInTheDocument();
    expect(
      screen.queryByText("No PT bookings are in this range."),
    ).not.toBeInTheDocument();
    await act(async () => {
      pendingList.resolve(page([]) as never);
      await pendingList.promise;
    });
    expect(
      await screen.findByText("No PT bookings are in this range."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Book a session to add it to this calendar."),
    ).toBeInTheDocument();
  });

  it("keeps booking actions behind schedule management", async () => {
    vi.mocked(listPtBookings).mockResolvedValue(page([booking()]));
    renderManager({ canManage: false });
    expect(await screen.findByText("BOOKED")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Book session" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Mark attended" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Cancel booking" }),
    ).not.toBeInTheDocument();
    cleanup();

    vi.mocked(listPtBookings).mockResolvedValue(page([]));
    renderManager({ canManage: false });
    expect(
      await screen.findByText("Sessions in this range will appear here."),
    ).toBeInTheDocument();
    cleanup();

    vi.mocked(listPtBookings).mockResolvedValue(
      page([booking({ status: "ATTENDED" })]),
    );
    renderManager();
    expect(await screen.findByText("ATTENDED")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Mark attended" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Mark no-show" }),
    ).not.toBeInTheDocument();
  });

  it("retries a failed calendar load", async () => {
    vi.mocked(listPtBookings).mockRejectedValue(
      new Error("Calendar unavailable."),
    );
    renderManager();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Calendar unavailable.",
    );
    vi.mocked(listPtBookings).mockResolvedValue(page([booking()]));
    fireEvent.click(
      screen.getByRole("button", { name: "Retry loading bookings" }),
    );
    expect(await screen.findByText("MEM-1 · Ada Member")).toBeInTheDocument();
  });

  it("applies the date range and member filter, then clears them", async () => {
    renderManager();
    await screen.findByText("No PT bookings are in this range.");
    fireEvent.change(screen.getByLabelText("From"), {
      target: { value: "2026-10-01" },
    });
    fireEvent.change(screen.getByLabelText("To"), {
      target: { value: "2026-10-03" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Show calendar" }));
    await waitFor(() =>
      expect(listPtBookings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          view: "calendar",
          date_from: "2026-10-01",
          date_to: "2026-10-03",
          limit: "25",
        }),
      ),
    );
    expect(
      await screen.findByText(
        "Try another gym-local range or clear the member filter.",
      ),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Filter bookings by member"), {
      target: { value: "Grace" },
    });
    fireEvent.click(
      await screen.findByRole("button", { name: "Grace Hopper (M-1)" }),
    );
    await waitFor(() =>
      expect(listPtBookings).toHaveBeenLastCalledWith(
        expect.objectContaining({ member_id: MEMBER, date_from: "2026-10-01" }),
      ),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Clear member filter" }),
    );
    await waitFor(() => {
      const last = vi.mocked(listPtBookings).mock.calls.at(-1)?.[0] as {
        member_id?: string;
      };
      expect(last.member_id).toBeUndefined();
    });

    fireEvent.click(screen.getByRole("button", { name: "Reset range" }));
    await waitFor(() =>
      expect(listPtBookings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          date_from: "2026-10-19",
          date_to: "2026-10-25",
        }),
      ),
    );
    expect(
      screen.queryByRole("button", { name: "Reset range" }),
    ).not.toBeInTheDocument();
  });

  it("pages the calendar with the cursor and ignores a stale range", async () => {
    vi.mocked(listPtBookings).mockResolvedValueOnce(
      page([booking()], "cursor-2"),
    );
    renderManager();
    fireEvent.click(await screen.findByRole("button", { name: "Next page" }));
    await waitFor(() =>
      expect(listPtBookings).toHaveBeenLastCalledWith(
        expect.objectContaining({ after: "cursor-2" }),
      ),
    );
    cleanup();

    const first = deferred<never>();
    const second = deferred<never>();
    let calls = 0;
    vi.mocked(listPtBookings).mockImplementation(() => {
      calls += 1;
      return (calls === 1 ? first.promise : second.promise) as never;
    });
    renderManager();
    await waitFor(() => expect(calls).toBe(1));
    fireEvent.change(screen.getByLabelText("From"), {
      target: { value: "2026-11-01" },
    });
    fireEvent.change(screen.getByLabelText("To"), {
      target: { value: "2026-11-02" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Show calendar" }));
    await waitFor(() => expect(calls).toBe(2));
    await act(async () => {
      second.resolve(
        page([
          booking({
            id: SECOND,
            member_id: OTHER,
            member_name: "MEM-2 · Other Member",
          }),
        ]) as never,
      );
      await second.promise;
    });
    expect(await screen.findByText("MEM-2 · Other Member")).toBeInTheDocument();
    await act(async () => {
      first.resolve(page([booking()]) as never);
      await first.promise;
    });
    expect(screen.queryByText("MEM-1 · Ada Member")).not.toBeInTheDocument();
    expect(screen.getByText("MEM-2 · Other Member")).toBeInTheDocument();
  });
});

describe("book session dialog", () => {
  it("opens a labelled modal and closes from cancel, close, escape, and the backdrop", async () => {
    renderManager();
    await screen.findByText("No PT bookings are in this range.");
    expect(
      screen.queryByRole("dialog", { name: "Book session" }),
    ).not.toBeInTheDocument();
    const dialog = await openBookDialog();
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAttribute("aria-labelledby", "book-pt-title");
    expect(dialog).toHaveAttribute("aria-describedby", "book-pt-description");
    expect(dialog).toHaveAttribute("id", "book-pt-dialog");
    expect(dialog).toHaveClass(
      "flex",
      "w-full",
      "flex-col",
      "overflow-y-auto",
      "sm:max-w-2xl",
    );
    expect(dialog.className).toContain("sm:max-h-[min(90dvh,48rem)]");
    expect(dialog.querySelector(".sticky.top-0")).not.toBeNull();
    expect(dialog.querySelector(".sticky.bottom-0")).not.toBeNull();
    expect(
      within(dialog).queryByRole("checkbox", {
        name: "Unmetered manager booking",
      }),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).getByRole("heading", { name: "Book session" }),
    ).toHaveAttribute("id", "book-pt-title");
    expect(
      screen.getByRole("button", { hidden: true, name: "Book session" }),
    ).toHaveAttribute("aria-controls", "book-pt-dialog");
    const backdrop = dialog.parentElement;
    if (!backdrop) throw new Error("Missing book session backdrop.");
    expect(backdrop).toHaveAttribute("role", "presentation");
    expect(backdrop).toHaveClass("fixed", "inset-0", "sm:place-items-center");

    fireEvent.mouseDown(within(dialog).getByLabelText("Trainer"));
    expect(screen.getByRole("dialog", { name: "Book session" })).toBe(dialog);

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(
      screen.queryByRole("dialog", { name: "Book session" }),
    ).not.toBeInTheDocument();

    const closed = await openBookDialog();
    fireEvent.click(within(closed).getByRole("button", { name: "Close" }));
    expect(
      screen.queryByRole("dialog", { name: "Book session" }),
    ).not.toBeInTheDocument();

    const escaped = await openBookDialog();
    fireEvent.keyDown(escaped, { key: "Escape" });
    expect(
      screen.queryByRole("dialog", { name: "Book session" }),
    ).not.toBeInTheDocument();

    const dismissed = await openBookDialog();
    fireEvent.mouseDown(dismissed.parentElement ?? dismissed);
    expect(
      screen.queryByRole("dialog", { name: "Book session" }),
    ).not.toBeInTheDocument();
  });

  it("keeps create errors in the dialog and ignores a stale package list", async () => {
    const firstPackages = deferred<never>();
    const secondPackages = deferred<never>();
    vi.mocked(listMemberPackages)
      .mockReturnValueOnce(firstPackages.promise as never)
      .mockReturnValueOnce(secondPackages.promise as never);
    renderManager();
    const dialog = await openBookDialog();
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save booking" }),
    );
    const memberError = within(dialog).getByRole("alert");
    expect(memberError).toHaveTextContent("Select an active member.");
    expect(dialog).toContainElement(memberError);
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(createPtBooking).not.toHaveBeenCalled();

    await chooseGrace(dialog);
    fireEvent.change(within(dialog).getByLabelText("Search active members"), {
      target: { value: "Ada" },
    });
    fireEvent.click(
      await within(dialog).findByRole("button", { name: "Ada Lovelace (M-2)" }),
    );
    await act(async () => {
      secondPackages.resolve(
        page([
          {
            id: OTHER_GRANT,
            package_code: "PT2",
            package_name: "Two",
            remaining_sessions: 2,
            status: "ACTIVE",
          },
        ]) as never,
      );
      await secondPackages.promise;
    });
    expect(
      await within(dialog).findByRole("option", { name: /PT2/ }),
    ).toBeInTheDocument();
    await act(async () => {
      firstPackages.resolve(
        page([
          {
            id: GRANT,
            package_code: "PT10",
            package_name: "Ten",
            remaining_sessions: 4,
            status: "ACTIVE",
          },
        ]) as never,
      );
      await firstPackages.promise;
    });
    expect(
      within(dialog).queryByRole("option", { name: /PT10/ }),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).getByRole("option", { name: /PT2/ }),
    ).toBeInTheDocument();
    expect(listMemberPackages).toHaveBeenLastCalledWith(
      expect.objectContaining({
        member_id: OTHER,
        package_type: "PT",
        status: "ACTIVE",
        limit: "100",
      }),
    );

    fireEvent.change(within(dialog).getByLabelText("Starts"), {
      target: { value: "not-a-time" },
    });
    fireEvent.change(within(dialog).getByLabelText("Trainer"), {
      target: { value: TRAINER },
    });
    fireEvent.change(within(dialog).getByLabelText("Member package"), {
      target: { value: OTHER_GRANT },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save booking" }),
    );
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Enter a valid time.",
    );
    expect(createPtBooking).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "Book session" })).toBe(dialog);
  });

  it("reuses the idempotency key until the same booking is saved", async () => {
    vi.mocked(createPtBooking)
      .mockRejectedValueOnce(new Error("Trainer is not eligible."))
      .mockRejectedValueOnce(new Error("Trainer is not eligible."))
      .mockResolvedValue(booking() as never);
    renderManager({ timeZone: "Asia/Bangkok" });
    await screen.findByText("No PT bookings are in this range.");
    const listCalls = vi.mocked(listPtBookings).mock.calls.length;
    const dialog = await openBookDialog();
    await within(dialog).findByRole("option", { name: /Ada/ });
    fireEvent.change(within(dialog).getByLabelText("Trainer"), {
      target: { value: TRAINER },
    });
    await chooseGrace(dialog);
    await within(dialog).findByRole("option", { name: /PT10/ });
    fireEvent.change(within(dialog).getByLabelText("Member package"), {
      target: { value: GRANT },
    });
    fireEvent.change(within(dialog).getByLabelText("Starts"), {
      target: { value: "2026-10-20T09:00" },
    });
    fireEvent.change(within(dialog).getByLabelText("Ends"), {
      target: { value: "2026-10-20T10:00" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save booking" }),
    );
    await waitFor(() => expect(createPtBooking).toHaveBeenCalledTimes(1));
    const firstKey = vi.mocked(createPtBooking).mock.calls[0]?.[1];
    expect(firstKey).toMatch(KEY);
    expect(vi.mocked(createPtBooking).mock.calls[0]?.[0]).toEqual(
      expect.objectContaining({
        trainer_staff_profile_id: TRAINER,
        member_id: MEMBER,
        member_service_package_id: GRANT,
        starts_at: "2026-10-20T02:00:00.000Z",
        ends_at: "2026-10-20T03:00:00.000Z",
        unmetered: false,
        unmetered_reason: "",
      }),
    );
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Trainer is not eligible.",
    );
    expect(screen.getByRole("dialog", { name: "Book session" })).toBe(dialog);

    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save booking" }),
    );
    await waitFor(() => expect(createPtBooking).toHaveBeenCalledTimes(2));
    expect(vi.mocked(createPtBooking).mock.calls[1]?.[1]).toBe(firstKey);

    fireEvent.change(within(dialog).getByLabelText("Ends"), {
      target: { value: "2026-10-20T11:00" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save booking" }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Book session" }),
      ).not.toBeInTheDocument(),
    );
    const savedKey = vi.mocked(createPtBooking).mock.calls[2]?.[1];
    expect(savedKey).toMatch(KEY);
    expect(savedKey).not.toBe(firstKey);
    expect(vi.mocked(createPtBooking).mock.calls[2]?.[0]).toEqual(
      expect.objectContaining({ ends_at: "2026-10-20T04:00:00.000Z" }),
    );
    expect(screen.getByText("Session booked.")).toHaveAttribute(
      "role",
      "status",
    );
    await waitFor(() =>
      expect(vi.mocked(listPtBookings).mock.calls.length).toBeGreaterThan(
        listCalls,
      ),
    );
  });

  it("ignores dismissal and a duplicate save while booking is pending", async () => {
    const pendingSave = deferred<never>();
    vi.mocked(createPtBooking).mockReturnValue(pendingSave.promise as never);
    renderManager();
    const dialog = await openBookDialog();
    await within(dialog).findByRole("option", { name: /Ada/ });
    fireEvent.change(within(dialog).getByLabelText("Trainer"), {
      target: { value: TRAINER },
    });
    await chooseGrace(dialog);
    await within(dialog).findByRole("option", { name: /PT10/ });
    fireEvent.change(within(dialog).getByLabelText("Member package"), {
      target: { value: GRANT },
    });
    fireEvent.change(within(dialog).getByLabelText("Starts"), {
      target: { value: "2026-10-20T09:00" },
    });
    fireEvent.change(within(dialog).getByLabelText("Ends"), {
      target: { value: "2026-10-20T10:00" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save booking" }),
    );
    await waitFor(() => expect(createPtBooking).toHaveBeenCalledTimes(1));
    expect(dialog).toHaveAttribute("aria-busy", "true");
    expect(
      within(dialog).getByRole("button", { name: "Close" }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "Cancel" }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "Save booking" }),
    ).toBeDisabled();
    expect(within(dialog).getByLabelText("Trainer")).toBeDisabled();

    fireEvent.keyDown(dialog, { key: "Escape" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    fireEvent.mouseDown(dialog.parentElement ?? dialog);
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save booking" }),
    );
    expect(screen.getByRole("dialog", { name: "Book session" })).toBe(dialog);
    expect(createPtBooking).toHaveBeenCalledTimes(1);

    await act(async () => {
      pendingSave.resolve(booking() as never);
      await pendingSave.promise;
    });
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Book session" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByText("Session booked.")).toBeInTheDocument();
  });

  it("books an unmetered session without a package id", async () => {
    vi.mocked(createPtBooking).mockResolvedValue(booking() as never);
    renderManager({ canUnmetered: true });
    const dialog = await openBookDialog();
    expect(
      within(dialog).queryByRole("checkbox", {
        name: "Unmetered manager booking",
      }),
    ).toBeInTheDocument();
    fireEvent.click(
      within(dialog).getByRole("checkbox", {
        name: "Unmetered manager booking",
      }),
    );
    expect(
      within(dialog).queryByLabelText("Member package"),
    ).not.toBeInTheDocument();
    await within(dialog).findByRole("option", { name: /Ada/ });
    fireEvent.change(within(dialog).getByLabelText("Trainer"), {
      target: { value: TRAINER },
    });
    await chooseGrace(dialog);
    fireEvent.change(within(dialog).getByLabelText("Starts"), {
      target: { value: "2026-10-20T09:00" },
    });
    fireEvent.change(within(dialog).getByLabelText("Ends"), {
      target: { value: "2026-10-20T10:00" },
    });
    fireEvent.change(within(dialog).getByLabelText("Unmetered reason"), {
      target: { value: "Manager comp" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save booking" }),
    );
    await waitFor(() => expect(createPtBooking).toHaveBeenCalledTimes(1));
    const body = vi.mocked(createPtBooking).mock.calls[0]?.[0];
    expect(body).toEqual(
      expect.objectContaining({
        unmetered: true,
        unmetered_reason: "Manager comp",
        member_id: MEMBER,
        trainer_staff_profile_id: TRAINER,
      }),
    );
    expect(body).not.toHaveProperty("member_service_package_id");
  });
});

describe("booking commands", () => {
  it("attends, marks no-show, and cancels with the entered reason", async () => {
    vi.mocked(listPtBookings).mockResolvedValue(
      page([booking({ unmetered: true, unmetered_reason: "Comp" })]),
    );
    vi.mocked(attendPtBooking).mockResolvedValue(booking() as never);
    vi.mocked(noShowPtBooking).mockResolvedValue(booking() as never);
    vi.mocked(cancelPtBooking).mockResolvedValue(booking() as never);
    renderManager();
    expect(await screen.findByText("Unmetered · Comp")).toBeInTheDocument();
    const listCalls = vi.mocked(listPtBookings).mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Mark attended" }));
    await waitFor(() =>
      expect(attendPtBooking).toHaveBeenCalledWith(
        BOOKING,
        expect.stringMatching(KEY),
      ),
    );
    expect(screen.getByText("Attendance recorded.")).toHaveAttribute(
      "role",
      "status",
    );
    fireEvent.click(screen.getByRole("button", { name: "Mark no-show" }));
    await waitFor(() =>
      expect(noShowPtBooking).toHaveBeenCalledWith(
        BOOKING,
        expect.stringMatching(KEY),
      ),
    );
    expect(screen.getByText("No-show recorded.")).toBeInTheDocument();
    expect(vi.mocked(noShowPtBooking).mock.calls[0]?.[1]).not.toBe(
      vi.mocked(attendPtBooking).mock.calls[0]?.[1],
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel booking" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Cancel PT booking",
    });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAttribute(
      "aria-labelledby",
      "cancel-pt-booking-title",
    );
    expect(dialog).toHaveAttribute("id", "cancel-pt-booking-dialog");
    expect(
      within(dialog).getByText(
        "The reason is optional. Only the text entered here is sent.",
      ),
    ).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Cancellation reason"), {
      target: { value: "Member asked" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", {
        name: "Confirm booking cancellation",
      }),
    );
    await waitFor(() =>
      expect(cancelPtBooking).toHaveBeenCalledWith(
        BOOKING,
        { reason: "Member asked" },
        expect.stringMatching(KEY),
      ),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Cancel PT booking" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByText("Booking cancelled.")).toBeInTheDocument();
    await waitFor(() =>
      expect(vi.mocked(listPtBookings).mock.calls.length).toBeGreaterThan(
        listCalls,
      ),
    );
  });

  it("keeps a failed attend on the page and reuses its idempotency key", async () => {
    vi.mocked(listPtBookings).mockResolvedValue(page([booking()]));
    vi.mocked(attendPtBooking).mockRejectedValue(
      new Error("Already attended."),
    );
    renderManager();
    fireEvent.click(
      await screen.findByRole("button", { name: "Mark attended" }),
    );
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Already attended.");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const firstKey = vi.mocked(attendPtBooking).mock.calls[0]?.[1];
    fireEvent.click(screen.getByRole("button", { name: "Mark attended" }));
    await waitFor(() => expect(attendPtBooking).toHaveBeenCalledTimes(2));
    expect(vi.mocked(attendPtBooking).mock.calls[1]?.[1]).toBe(firstKey);
  });

  it("sends an empty cancellation reason and ignores dismissal while pending", async () => {
    vi.mocked(listPtBookings).mockResolvedValue(page([booking()]));
    const pendingCancel = deferred<never>();
    vi.mocked(cancelPtBooking).mockReturnValue(pendingCancel.promise as never);
    renderManager();
    fireEvent.click(
      await screen.findByRole("button", { name: "Cancel booking" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Cancel PT booking",
    });
    fireEvent.click(
      within(dialog).getByRole("button", {
        name: "Confirm booking cancellation",
      }),
    );
    await waitFor(() => expect(cancelPtBooking).toHaveBeenCalledTimes(1));
    expect(cancelPtBooking).toHaveBeenCalledWith(
      BOOKING,
      { reason: "" },
      expect.stringMatching(KEY),
    );
    expect(dialog).toHaveAttribute("aria-busy", "true");
    fireEvent.keyDown(dialog, { key: "Escape" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    fireEvent.mouseDown(dialog.parentElement ?? dialog);
    fireEvent.click(
      within(dialog).getByRole("button", {
        name: "Confirm booking cancellation",
      }),
    );
    expect(screen.getByRole("dialog", { name: "Cancel PT booking" })).toBe(
      dialog,
    );
    expect(cancelPtBooking).toHaveBeenCalledTimes(1);

    await act(async () => {
      pendingCancel.resolve(booking() as never);
      await pendingCancel.promise;
    });
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Cancel PT booking" }),
      ).not.toBeInTheDocument(),
    );
  });
});
