import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  getGymSettings,
  listMembers,
  operationMutation,
} from "@/features/operations/client";
import { MembersManager } from "@/features/operations/members-manager";
import type {
  GymSettings,
  Member,
  MemberListPage,
  MemberListQuery,
} from "@/types/api";

vi.mock("@/features/operations/client", () => ({
  getGymSettings: vi.fn(),
  listMembers: vi.fn(),
  operationMutation: vi.fn(),
}));

const MEMBER_ID = "11111111-1111-4111-8111-111111111111";
const OTHER_ID = "22222222-2222-4222-8222-222222222222";
const THIRD_ID = "33333333-3333-4333-8333-333333333333";

function member(overrides: Partial<Member> = {}): Member {
  return {
    id: MEMBER_ID,
    member_number: "MEM-000001",
    first_name: "Ada",
    last_name: "Lovelace",
    normalized_full_name: "ada lovelace",
    phone: "555",
    email: "ada@example.test",
    gender: null,
    date_of_birth: null,
    address: {
      address_line1: "",
      address_line2: "",
      city: "",
      region: "",
      postal_code: "",
      country_code: "",
    },
    join_date: "2026-01-01",
    emergency_contact_name: "",
    emergency_contact_phone: "",
    notes: "",
    profile_photo_asset_id: null,
    linked_user_id: null,
    is_pt_client: false,
    authz_version: 1,
    status: "ACTIVE",
    archived_at: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function page(rows: Member[], next: string | null = null): MemberListPage {
  return {
    data: rows,
    meta: { has_more: next !== null, next_after: next },
  };
}

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  let reject: (reason?: unknown) => void = () => undefined;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("Members manager", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listMembers).mockResolvedValue(page([member()]));
    vi.mocked(operationMutation).mockResolvedValue(member());
  });

  it("shows a loading state and then the published member fields", async () => {
    const pending = deferred<MemberListPage>();
    vi.mocked(listMembers).mockReturnValue(pending.promise);
    render(<MembersManager canCreate defaultDate="2026-10-17" />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading members.");
    expect(screen.queryByText("No members found.")).not.toBeInTheDocument();
    pending.resolve(page([member()]));

    expect(
      await screen.findByRole("link", { name: "Ada Lovelace" }),
    ).toHaveAttribute("href", `/members/${MEMBER_ID}`);
    expect(screen.getByText("MEM-000001")).toBeInTheDocument();
    expect(screen.getByText("555")).toBeInTheDocument();
    expect(screen.getByText("2026-01-01")).toBeInTheDocument();
    expect(screen.getByText("ACTIVE")).toBeInTheDocument();
    expect(screen.queryByText("ada lovelace")).not.toBeInTheDocument();
    expect(listMembers).toHaveBeenCalledWith({ limit: 25 });
  });

  it("loads the next page, dedupes ids, and stops a repeated cursor", async () => {
    const grace = member({
      id: OTHER_ID,
      first_name: "Grace",
      last_name: "Hopper",
      member_number: "MEM-000002",
    });
    vi.mocked(listMembers)
      .mockResolvedValueOnce(page([member(), member()], "cursor.next"))
      .mockResolvedValueOnce(page([member(), grace], "cursor.next"));
    render(<MembersManager canCreate defaultDate="2026-10-17" />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Load more members" }),
    );

    expect(
      await screen.findByRole("link", { name: "Grace Hopper" }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Ada Lovelace" })).toHaveLength(
      1,
    );
    expect(
      screen.queryByRole("button", { name: "Load more members" }),
    ).not.toBeInTheDocument();
    expect(listMembers).toHaveBeenNthCalledWith(2, {
      limit: 25,
      after: "cursor.next",
    });
    expect(listMembers).toHaveBeenCalledTimes(2);
  });

  it("resets the page when filters change and ignores a stale response", async () => {
    const requests: Array<ReturnType<typeof deferred<MemberListPage>>> = [];
    vi.mocked(listMembers).mockImplementation(
      async (query: MemberListQuery = {}) => {
        const pending = deferred<MemberListPage>();
        requests.push(pending);
        pending.promise.then(
          () => undefined,
          () => undefined,
        );
        void query;
        return pending.promise;
      },
    );
    render(<MembersManager canCreate defaultDate="2026-10-17" />);
    await waitFor(() => expect(requests).toHaveLength(1));
    requests[0]?.resolve(page([member()], "cursor.old"));
    expect(
      await screen.findByRole("link", { name: "Ada Lovelace" }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Load more members" }));
    await waitFor(() => expect(requests).toHaveLength(2));
    fireEvent.change(screen.getByLabelText("Search members"), {
      target: { value: "A" },
    });
    fireEvent.change(screen.getByLabelText("Search members"), {
      target: { value: "Grace" },
    });
    fireEvent.click(screen.getByRole("radio", { name: "Active" }));
    fireEvent.change(screen.getByLabelText("Joined from"), {
      target: { value: "2026-01-01" },
    });
    fireEvent.change(screen.getByLabelText("Joined to"), {
      target: { value: "2026-10-17" },
    });

    expect(
      screen.queryByRole("link", { name: "Ada Lovelace" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading members.");
    requests[1]?.resolve(
      page([
        member({
          id: THIRD_ID,
          first_name: "Stale",
          last_name: "Page",
        }),
      ]),
    );
    await waitFor(() => expect(requests.length).toBeGreaterThan(2));
    const staleList = requests[requests.length - 2];
    const current = requests[requests.length - 1];
    staleList?.resolve(
      page([member({ id: OTHER_ID, first_name: "Old", last_name: "Filter" })]),
    );
    expect(
      screen.queryByRole("link", { name: "Old Filter" }),
    ).not.toBeInTheDocument();
    current?.resolve(
      page([
        member({
          id: THIRD_ID,
          first_name: "Grace",
          last_name: "Hopper",
          member_number: "MEM-000099",
        }),
      ]),
    );

    expect(
      await screen.findByRole("link", { name: "Grace Hopper" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Old Filter" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Stale Page" }),
    ).not.toBeInTheDocument();
    expect(listMembers).toHaveBeenLastCalledWith({
      limit: 25,
      search: "Grace",
      status: "ACTIVE",
      joined_from: "2026-01-01",
      joined_to: "2026-10-17",
    });
  });

  it("filters active PT clients and omits the flag for all members", async () => {
    const requests: Array<ReturnType<typeof deferred<MemberListPage>>> = [];
    vi.mocked(listMembers).mockImplementation(async () => {
      const pending = deferred<MemberListPage>();
      requests.push(pending);
      pending.promise.then(
        () => undefined,
        () => undefined,
      );
      return pending.promise;
    });
    render(<MembersManager canCreate defaultDate="2026-10-17" />);

    const filter = screen.getByRole("combobox", {
      name: "Filter by PT client",
    });
    expect(filter).toHaveValue("");
    expect(
      Array.from((filter as HTMLSelectElement).options, (option) => [
        option.value,
        option.text,
      ]),
    ).toEqual([
      ["", "All members"],
      ["true", "Active PT clients"],
    ]);

    await waitFor(() => expect(requests).toHaveLength(1));
    expect(listMembers).toHaveBeenCalledWith({ limit: 25 });
    expect(vi.mocked(listMembers).mock.calls[0]?.[0]).not.toHaveProperty(
      "is_pt_client",
    );
    requests[0]?.resolve(page([member()]));
    expect(
      await screen.findByRole("link", { name: "Ada Lovelace" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("PT")).not.toBeInTheDocument();

    fireEvent.change(filter, { target: { value: "true" } });
    expect(filter).toHaveValue("true");
    await waitFor(() => expect(requests).toHaveLength(2));
    expect(listMembers).toHaveBeenLastCalledWith({
      limit: 25,
      is_pt_client: true,
    });

    fireEvent.change(filter, { target: { value: "" } });
    expect(filter).toHaveValue("");
    expect(
      screen.queryByRole("link", { name: "Ada Lovelace" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading members.");
    await waitFor(() => expect(requests).toHaveLength(3));
    expect(vi.mocked(listMembers).mock.calls[2]?.[0]).toEqual({ limit: 25 });
    expect(vi.mocked(listMembers).mock.calls[2]?.[0]).not.toHaveProperty(
      "is_pt_client",
    );

    await act(async () => {
      requests[1]?.resolve(
        page([
          member({
            id: OTHER_ID,
            first_name: "Stale",
            last_name: "Client",
            is_pt_client: true,
          }),
        ]),
      );
    });
    expect(
      screen.queryByRole("link", { name: /Stale Client/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("PT")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading members.");

    requests[2]?.resolve(page([member()]));
    expect(
      await screen.findByRole("link", { name: "Ada Lovelace" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /Stale Client/ }),
    ).not.toBeInTheDocument();

    fireEvent.change(filter, { target: { value: "true" } });
    await waitFor(() => expect(requests).toHaveLength(4));
    expect(listMembers).toHaveBeenLastCalledWith({
      limit: 25,
      is_pt_client: true,
    });
    requests[3]?.resolve(
      page([
        member({
          id: THIRD_ID,
          first_name: "Grace",
          last_name: "Hopper",
          member_number: "MEM-000003",
          is_pt_client: true,
        }),
      ]),
    );
    expect(
      await screen.findByRole("link", { name: /Grace Hopper/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("PT")).toBeInTheDocument();
    expect(screen.queryByText("MEM-000001")).not.toBeInTheDocument();
  });

  it("shows an empty page, a retryable load error, and a retryable paging error", async () => {
    vi.mocked(listMembers).mockRejectedValueOnce(
      new Error("Members unavailable."),
    );
    render(<MembersManager canCreate defaultDate="2026-10-17" />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Members unavailable.",
    );
    expect(screen.queryByText("No members found.")).not.toBeInTheDocument();
    vi.mocked(listMembers).mockResolvedValueOnce(page([]));
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("No members found.")).toBeInTheDocument();

    vi.mocked(listMembers).mockResolvedValueOnce(
      page([member()], "cursor.next"),
    );
    fireEvent.change(screen.getByLabelText("Search members"), {
      target: { value: "Ada" },
    });
    const loadMore = await screen.findByRole("button", {
      name: "Load more members",
    });
    vi.mocked(listMembers).mockRejectedValueOnce(
      new Error("Page unavailable."),
    );
    fireEvent.click(loadMore);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Page unavailable.",
    );
    expect(
      screen.getByRole("link", { name: "Ada Lovelace" }),
    ).toBeInTheDocument();
    vi.mocked(listMembers).mockResolvedValueOnce(
      page([
        member({
          id: OTHER_ID,
          first_name: "Grace",
          last_name: "Hopper",
        }),
      ]),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Retry loading members" }),
    );
    expect(
      await screen.findByRole("link", { name: "Grace Hopper" }),
    ).toBeInTheDocument();
  });

  it("creates a member and replaces the loaded pages under the current filters", async () => {
    const grace = member({
      id: OTHER_ID,
      first_name: "Grace",
      last_name: "Hopper",
      member_number: "MEM-000002",
    });
    const hopper = member({
      id: THIRD_ID,
      first_name: "Grace",
      last_name: "Hopper",
      member_number: "MEM-000003",
      phone: "777",
      email: "grace@example.test",
    });
    vi.mocked(listMembers)
      .mockResolvedValueOnce(page([member()], "cursor.next"))
      .mockResolvedValueOnce(page([grace]))
      .mockResolvedValue(page([hopper]));
    render(<MembersManager canCreate defaultDate="2026-10-17" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Load more members" }),
    );
    expect(
      await screen.findByRole("link", { name: "Grace Hopper" }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: "Active" }));
    await waitFor(() =>
      expect(listMembers).toHaveBeenLastCalledWith({
        limit: 25,
        status: "ACTIVE",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Add member" }));
    fireEvent.change(screen.getByLabelText("First name"), {
      target: { value: "Grace" },
    });
    fireEvent.change(screen.getByLabelText("Last name"), {
      target: { value: "Hopper" },
    });
    fireEvent.change(screen.getByLabelText("Phone"), {
      target: { value: "777" },
    });
    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "grace@example.test" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save member" }));

    await waitFor(() =>
      expect(operationMutation).toHaveBeenCalledWith("members/", {
        member_number: "",
        first_name: "Grace",
        last_name: "Hopper",
        phone: "777",
        email: "grace@example.test",
        gender: null,
        date_of_birth: null,
        address: {
          address_line1: "",
          address_line2: "",
          city: "",
          region: "",
          postal_code: "",
          country_code: "",
        },
        join_date: "2026-10-17",
        emergency_contact_name: "",
        emergency_contact_phone: "",
        notes: "",
      }),
    );
    expect(operationMutation).toHaveBeenCalledTimes(1);
    const created = vi.mocked(operationMutation).mock.calls[0]?.[1] as Record<
      string,
      unknown
    >;
    expect(created).not.toHaveProperty("profile_photo_asset_id");
    expect(created).not.toHaveProperty("status");
    expect(created).not.toHaveProperty("gym_id");
    expect(created).not.toHaveProperty("id");
    expect(await screen.findByText("MEM-000003")).toBeInTheDocument();
    expect(screen.queryByText("MEM-000001")).not.toBeInTheDocument();
    expect(screen.queryByText("MEM-000002")).not.toBeInTheDocument();
    expect(listMembers).toHaveBeenLastCalledWith({
      limit: 25,
      status: "ACTIVE",
    });
    expect(vi.mocked(listMembers).mock.calls.at(-1)?.[0]).not.toHaveProperty(
      "after",
    );
  });

  it("loads archived members from the first page and resets an open cursor", async () => {
    const requests: Array<ReturnType<typeof deferred<MemberListPage>>> = [];
    vi.mocked(listMembers).mockImplementation(async () => {
      const pending = deferred<MemberListPage>();
      requests.push(pending);
      pending.promise.then(
        () => undefined,
        () => undefined,
      );
      return pending.promise;
    });
    render(<MembersManager canCreate canRestore defaultDate="2026-10-17" />);

    expect(
      screen.getAllByRole("radio").map((radio) => radio.textContent),
    ).toEqual([
      "All",
      "Active",
      "Expired",
      "Suspended",
      "Cancelled",
      "Archived",
    ]);
    expect(screen.getByRole("radio", { name: "All" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.queryByLabelText("Filter by status")).not.toBeInTheDocument();

    await waitFor(() => expect(requests).toHaveLength(1));
    requests[0]?.resolve(page([member()], "cursor.next"));
    expect(
      await screen.findByRole("link", { name: "Ada Lovelace" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Load more members" }));
    await waitFor(() => expect(requests).toHaveLength(2));
    expect(listMembers).toHaveBeenLastCalledWith({
      limit: 25,
      after: "cursor.next",
    });

    fireEvent.click(screen.getByRole("radio", { name: "Archived" }));
    expect(screen.getByRole("radio", { name: "Archived" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("radio", { name: "All" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(
      screen.queryByRole("link", { name: "Ada Lovelace" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Load more members" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading members.");

    requests[1]?.resolve(
      page([
        member({
          id: OTHER_ID,
          first_name: "Stale",
          last_name: "Page",
        }),
      ]),
    );
    expect(
      screen.queryByRole("link", { name: "Stale Page" }),
    ).not.toBeInTheDocument();

    await waitFor(() => expect(requests.length).toBeGreaterThan(2));
    expect(listMembers).toHaveBeenLastCalledWith({
      limit: 25,
      status: "ARCHIVED",
    });
    expect(vi.mocked(listMembers).mock.calls.at(-1)?.[0]).not.toHaveProperty(
      "after",
    );

    requests.at(-1)?.resolve(
      page([
        member({
          status: "ARCHIVED",
          archived_at: "2026-10-17T00:00:00Z",
        }),
      ]),
    );
    const row = await screen.findByRole("row", { name: /Ada Lovelace/ });
    expect(row).toHaveAttribute("data-status", "ARCHIVED");
    expect(row).toHaveClass("bg-slate-100");
    expect(
      within(row).getByRole("link", { name: "Ada Lovelace" }),
    ).toHaveAttribute("href", `/members/${MEMBER_ID}`);
    expect(
      within(row).getByRole("button", { name: "Restore Ada Lovelace" }),
    ).toHaveClass("button-success");
    expect(screen.getByText(/These members are archived/)).toBeInTheDocument();
    expect(screen.getByText(/record is not deleted/)).toBeInTheDocument();
  });

  it("restores an archived member after confirmation and leaves other rows", async () => {
    const archived = member({
      status: "ARCHIVED",
      archived_at: "2026-10-17T00:00:00Z",
    });
    const active = member({
      id: OTHER_ID,
      first_name: "Grace",
      last_name: "Hopper",
      member_number: "MEM-000002",
      status: "ACTIVE",
    });
    vi.mocked(listMembers).mockResolvedValue(page([archived, active]));
    vi.mocked(operationMutation).mockResolvedValue(
      member({
        status: "ACTIVE",
        archived_at: null,
        authz_version: 3,
      }),
    );
    render(
      <MembersManager canCreate={false} canRestore defaultDate="2026-10-17" />,
    );

    expect(
      await screen.findByRole("button", { name: "Restore Ada Lovelace" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Restore Grace Hopper" }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: "Archived" }));
    await waitFor(() =>
      expect(listMembers).toHaveBeenLastCalledWith({
        limit: 25,
        status: "ARCHIVED",
      }),
    );
    expect(
      await screen.findByRole("link", { name: "Ada Lovelace" }),
    ).toHaveAttribute("href", `/members/${MEMBER_ID}`);

    fireEvent.click(
      screen.getByRole("button", { name: "Restore Ada Lovelace" }),
    );
    const dialog = screen.getByRole("dialog", {
      name: "Restore Ada Lovelace (MEM-000001)?",
    });
    expect(dialog).toHaveTextContent("not deleted");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(operationMutation).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("dialog", {
        name: "Restore Ada Lovelace (MEM-000001)?",
      }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Ada Lovelace" }),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Restore Ada Lovelace" }),
    );
    fireEvent.click(
      within(
        screen.getByRole("dialog", {
          name: "Restore Ada Lovelace (MEM-000001)?",
        }),
      ).getByRole("button", { name: "Restore" }),
    );

    await waitFor(() =>
      expect(operationMutation).toHaveBeenCalledWith(
        `members/${MEMBER_ID}/restore/`,
        {},
      ),
    );
    expect(operationMutation).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByRole("link", { name: "Ada Lovelace" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Grace Hopper" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Ada Lovelace restored to Active."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Delete/ }),
    ).not.toBeInTheDocument();
  });

  it("keeps the archived member visible when restore fails", async () => {
    vi.mocked(listMembers).mockResolvedValue(
      page([
        member({
          status: "ARCHIVED",
          archived_at: "2026-10-17T00:00:00Z",
        }),
      ]),
    );
    vi.mocked(operationMutation).mockRejectedValue(
      new Error("Restore was rejected."),
    );
    render(
      <MembersManager canCreate={false} canRestore defaultDate="2026-10-17" />,
    );
    fireEvent.click(screen.getByRole("radio", { name: "Archived" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Restore Ada Lovelace" }),
    );
    fireEvent.click(
      within(
        screen.getByRole("dialog", {
          name: "Restore Ada Lovelace (MEM-000001)?",
        }),
      ).getByRole("button", { name: "Restore" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Restore was rejected.",
    );
    expect(
      screen.getByRole("link", { name: "Ada Lovelace" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Restore Ada Lovelace" }),
    ).toBeEnabled();
    expect(operationMutation).toHaveBeenCalledTimes(1);
  });

  it("does not offer restore without members.restore", async () => {
    vi.mocked(listMembers).mockResolvedValue(
      page([
        member({
          status: "ARCHIVED",
          archived_at: "2026-10-17T00:00:00Z",
        }),
      ]),
    );
    render(<MembersManager canCreate={false} defaultDate="2026-10-17" />);
    fireEvent.click(screen.getByRole("radio", { name: "Archived" }));
    await waitFor(() =>
      expect(listMembers).toHaveBeenLastCalledWith({
        limit: 25,
        status: "ARCHIVED",
      }),
    );
    expect(
      await screen.findByRole("link", { name: "Ada Lovelace" }),
    ).toHaveAttribute("href", `/members/${MEMBER_ID}`);
    expect(screen.getByRole("row", { name: /Ada Lovelace/ })).toHaveAttribute(
      "data-status",
      "ARCHIVED",
    );
    expect(
      screen.queryByRole("button", { name: /Restore/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Delete/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(/Open a name for the full profile/),
    ).toBeInTheDocument();
  });

  it("hides the create form without members.create", async () => {
    render(<MembersManager canCreate={false} defaultDate="2026-10-17" />);
    expect(await screen.findByText("MEM-000001")).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Add member" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Save member" }),
    ).not.toBeInTheDocument();
  });

  it("normalizes a full create form and blank nullable fields", async () => {
    render(<MembersManager canCreate defaultDate="2026-10-17" />);
    expect(await screen.findByText("MEM-000001")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add member" }));

    const memberNumber = screen.getByLabelText("Member number");
    expect(memberNumber).toBeDisabled();
    expect(memberNumber).toHaveValue("");
    expect(
      screen.getByText("The gym assigns the next number when you save."),
    ).toBeInTheDocument();
    expect(getGymSettings).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("First name"), {
      target: { value: "  Grace   Hopper " },
    });
    fireEvent.change(screen.getByLabelText("Last name"), {
      target: { value: "  " },
    });
    fireEvent.change(screen.getByLabelText("Phone"), {
      target: { value: "   " },
    });
    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "   " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save member" }));

    expect(operationMutation).not.toHaveBeenCalled();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter a first name, last name, and join date.",
    );

    fireEvent.change(screen.getByLabelText("First name"), {
      target: { value: "  Grace  " },
    });
    fireEvent.change(screen.getByLabelText("Last name"), {
      target: { value: "  Hopper  " },
    });
    fireEvent.change(screen.getByLabelText("Phone"), {
      target: { value: "  +1 555  " },
    });
    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "  Grace@Example.test  " },
    });
    fireEvent.change(screen.getByLabelText("Gender"), {
      target: { value: "FEMALE" },
    });
    fireEvent.change(screen.getByLabelText("Date of birth"), {
      target: { value: "1990-04-02" },
    });
    fireEvent.change(screen.getByLabelText("Address line 1"), {
      target: { value: "  1  Main  " },
    });
    fireEvent.change(screen.getByLabelText("Address line 2"), {
      target: { value: "  Apt 2  " },
    });
    fireEvent.change(screen.getByLabelText("City"), {
      target: { value: "  Austin  " },
    });
    fireEvent.change(screen.getByLabelText("Region"), {
      target: { value: "  Texas  " },
    });
    fireEvent.change(screen.getByLabelText("Postal code"), {
      target: { value: "  78701  " },
    });
    fireEvent.change(screen.getByLabelText("Country code"), {
      target: { value: "  us  " },
    });
    fireEvent.change(screen.getByLabelText("Join date"), {
      target: { value: "2026-02-01" },
    });
    fireEvent.change(screen.getByLabelText("Emergency contact name"), {
      target: { value: "  Pat  Lee  " },
    });
    fireEvent.change(screen.getByLabelText("Emergency contact phone"), {
      target: { value: "  999  " },
    });
    fireEvent.change(screen.getByLabelText("Notes"), {
      target: { value: "  Evening visits  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save member" }));

    await waitFor(() =>
      expect(operationMutation).toHaveBeenCalledWith("members/", {
        member_number: "",
        first_name: "Grace",
        last_name: "Hopper",
        phone: "+1 555",
        email: "grace@example.test",
        gender: "FEMALE",
        date_of_birth: "1990-04-02",
        address: {
          address_line1: "1 Main",
          address_line2: "Apt 2",
          city: "Austin",
          region: "Texas",
          postal_code: "78701",
          country_code: "US",
        },
        join_date: "2026-02-01",
        emergency_contact_name: "Pat Lee",
        emergency_contact_phone: "999",
        notes: "Evening visits",
      }),
    );
  });

  it("shows the next gym number and keeps it out of the create request", async () => {
    vi.mocked(getGymSettings).mockResolvedValue({
      member_number_prefix: "NORTH",
      next_member_number: 7,
    } as GymSettings);
    render(
      <MembersManager
        canCreate
        canPreviewMemberNumber
        defaultDate="2026-10-17"
      />,
    );
    expect(await screen.findByText("MEM-000001")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add member" }));

    const memberNumber = screen.getByLabelText("Member number");
    expect(memberNumber).toBeDisabled();
    expect(await screen.findByText("Assigned by the gym.")).toBeInTheDocument();
    expect(memberNumber).toHaveValue("NORTH-000007");
    expect(getGymSettings).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByLabelText("First name"), {
      target: { value: "Grace" },
    });
    fireEvent.change(screen.getByLabelText("Last name"), {
      target: { value: "Hopper" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save member" }));

    await waitFor(() =>
      expect(operationMutation).toHaveBeenCalledWith(
        "members/",
        expect.objectContaining({
          member_number: "",
          first_name: "Grace",
          last_name: "Hopper",
        }),
      ),
    );
  });
});

describe("Members directory archive", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listMembers).mockResolvedValue(page([member()]));
    vi.mocked(operationMutation).mockResolvedValue(member());
  });

  function archivedMember() {
    return member({
      status: "ARCHIVED",
      archived_at: "2026-10-17T00:00:00Z",
    });
  }

  it("shows archive only for a current member when the caller can archive", async () => {
    const view = render(
      <MembersManager canCreate={false} defaultDate="2026-10-17" />,
    );
    expect(await screen.findByText("MEM-000001")).toBeInTheDocument();
    expect(screen.getAllByRole("columnheader")).toHaveLength(4);
    expect(
      screen.queryByRole("columnheader", { name: "Action" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Archive|Restore|Delete/ }),
    ).not.toBeInTheDocument();

    view.rerender(
      <MembersManager canArchive canCreate={false} defaultDate="2026-10-17" />,
    );
    expect(screen.getAllByRole("columnheader")).toHaveLength(5);
    expect(
      screen.getByRole("columnheader", { name: "Action" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Archive Ada Lovelace" }),
    ).toHaveClass("button-archive");
    expect(
      screen.queryByRole("button", { name: /Restore|Delete/ }),
    ).not.toBeInTheDocument();
    view.unmount();

    vi.mocked(listMembers).mockResolvedValue(page([archivedMember()]));
    render(
      <MembersManager canArchive canCreate={false} defaultDate="2026-10-17" />,
    );
    fireEvent.click(screen.getByRole("radio", { name: "Archived" }));
    expect(
      await screen.findByRole("link", { name: "Ada Lovelace" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Archive|Restore|Delete/ }),
    ).not.toBeInTheDocument();
  });

  it("does not archive when confirmation is cancelled", async () => {
    render(
      <MembersManager canArchive canCreate={false} defaultDate="2026-10-17" />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Archive Ada Lovelace" }),
    );
    const dialog = screen.getByRole("dialog", {
      name: "Archive Ada Lovelace (MEM-000001)?",
    });
    expect(dialog).toHaveTextContent("not deleted");
    expect(dialog).toHaveTextContent("history remain");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    expect(operationMutation).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Archive Ada Lovelace" }),
    ).toBeEnabled();

    fireEvent.click(
      screen.getByRole("button", { name: "Archive Ada Lovelace" }),
    );
    fireEvent.keyDown(
      screen.getByRole("dialog", {
        name: "Archive Ada Lovelace (MEM-000001)?",
      }),
      { key: "Escape" },
    );
    expect(operationMutation).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText("ACTIVE")).toBeInTheDocument();
  });

  it("removes the row from a status view and leaves the other member", async () => {
    vi.mocked(listMembers).mockResolvedValue(
      page([
        member(),
        member({
          id: OTHER_ID,
          first_name: "Grace",
          last_name: "Hopper",
          member_number: "MEM-000002",
        }),
      ]),
    );
    vi.mocked(operationMutation).mockResolvedValue(archivedMember());
    render(
      <MembersManager canArchive canCreate={false} defaultDate="2026-10-17" />,
    );
    fireEvent.click(await screen.findByRole("radio", { name: "Active" }));
    await waitFor(() =>
      expect(listMembers).toHaveBeenLastCalledWith({
        limit: 25,
        status: "ACTIVE",
      }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Archive Ada Lovelace" }),
    );
    fireEvent.click(
      within(
        screen.getByRole("dialog", {
          name: "Archive Ada Lovelace (MEM-000001)?",
        }),
      ).getByRole("button", { name: "Archive" }),
    );

    await waitFor(() =>
      expect(operationMutation).toHaveBeenCalledWith(
        `members/${MEMBER_ID}/archive/`,
        {},
      ),
    );
    expect(operationMutation).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByRole("link", { name: "Ada Lovelace" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Grace Hopper" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Ada Lovelace archived.")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Delete/ }),
    ).not.toBeInTheDocument();
  });

  it("updates the all-directory row and offers restore when permitted", async () => {
    vi.mocked(listMembers).mockResolvedValue(
      page([
        member(),
        member({
          id: OTHER_ID,
          first_name: "Grace",
          last_name: "Hopper",
          member_number: "MEM-000002",
        }),
      ]),
    );
    vi.mocked(operationMutation).mockResolvedValue(archivedMember());
    render(
      <MembersManager
        canArchive
        canCreate={false}
        canRestore
        defaultDate="2026-10-17"
      />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Archive Ada Lovelace" }),
    );
    fireEvent.click(
      within(
        screen.getByRole("dialog", {
          name: "Archive Ada Lovelace (MEM-000001)?",
        }),
      ).getByRole("button", { name: "Archive" }),
    );

    const row = await screen.findByRole("row", { name: /Ada Lovelace/ });
    expect(row).toHaveAttribute("data-status", "ARCHIVED");
    expect(
      within(row).queryByRole("button", { name: "Archive Ada Lovelace" }),
    ).not.toBeInTheDocument();
    expect(
      within(row).getByRole("button", { name: "Restore Ada Lovelace" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Archive Grace Hopper" }),
    ).toBeEnabled();
    expect(screen.getByText("Ada Lovelace archived.")).toBeInTheDocument();
    expect(operationMutation).toHaveBeenCalledTimes(1);
  });

  it("ignores a second click while archive is pending", async () => {
    const pending = deferred<Member>();
    vi.mocked(operationMutation).mockReturnValue(pending.promise);
    render(
      <MembersManager canArchive canCreate={false} defaultDate="2026-10-17" />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Archive Ada Lovelace" }),
    );
    const confirm = within(
      screen.getByRole("dialog", {
        name: "Archive Ada Lovelace (MEM-000001)?",
      }),
    ).getByRole("button", { name: "Archive" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);

    expect(operationMutation).toHaveBeenCalledTimes(1);
    const rowButton = await screen.findByRole("button", {
      name: "Archive Ada Lovelace",
    });
    expect(rowButton).toBeDisabled();
    fireEvent.click(rowButton);
    expect(operationMutation).toHaveBeenCalledTimes(1);

    pending.resolve(archivedMember());
    expect(
      await screen.findByText("Ada Lovelace archived."),
    ).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /Ada Lovelace/ })).toHaveAttribute(
      "data-status",
      "ARCHIVED",
    );
    expect(operationMutation).toHaveBeenCalledTimes(1);
  });

  it("keeps the member and shows an alert when archive fails", async () => {
    vi.mocked(operationMutation).mockRejectedValue(
      new Error("Archive was rejected."),
    );
    render(
      <MembersManager canArchive canCreate={false} defaultDate="2026-10-17" />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Archive Ada Lovelace" }),
    );
    fireEvent.click(
      within(
        screen.getByRole("dialog", {
          name: "Archive Ada Lovelace (MEM-000001)?",
        }),
      ).getByRole("button", { name: "Archive" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Archive was rejected.",
    );
    expect(
      screen.getByRole("link", { name: "Ada Lovelace" }),
    ).toBeInTheDocument();
    expect(screen.getByText("ACTIVE")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Archive Ada Lovelace" }),
    ).toBeEnabled();
    expect(
      screen.queryByText("Ada Lovelace archived."),
    ).not.toBeInTheDocument();
    expect(operationMutation).toHaveBeenCalledTimes(1);
  });

  it("ignores a stale archive response after the directory changes", async () => {
    const requests: Array<ReturnType<typeof deferred<MemberListPage>>> = [];
    vi.mocked(listMembers).mockImplementation(async () => {
      const pending = deferred<MemberListPage>();
      requests.push(pending);
      pending.promise.then(
        () => undefined,
        () => undefined,
      );
      return pending.promise;
    });
    const archive = deferred<Member>();
    vi.mocked(operationMutation).mockReturnValue(archive.promise);
    render(
      <MembersManager canArchive canCreate={false} defaultDate="2026-10-17" />,
    );

    await waitFor(() => expect(requests).toHaveLength(1));
    requests[0]?.resolve(page([member()]));
    fireEvent.click(
      await screen.findByRole("button", { name: "Archive Ada Lovelace" }),
    );
    fireEvent.click(
      within(
        screen.getByRole("dialog", {
          name: "Archive Ada Lovelace (MEM-000001)?",
        }),
      ).getByRole("button", { name: "Archive" }),
    );
    await waitFor(() => expect(operationMutation).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("radio", { name: "Active" }));
    await waitFor(() => expect(requests.length).toBeGreaterThan(1));
    await act(async () => {
      archive.resolve(archivedMember());
      await archive.promise;
    });
    requests.at(-1)?.resolve(
      page([
        member({
          id: OTHER_ID,
          first_name: "Grace",
          last_name: "Hopper",
          member_number: "MEM-000002",
        }),
      ]),
    );

    expect(
      await screen.findByRole("link", { name: "Grace Hopper" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Ada Lovelace" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("Ada Lovelace archived."),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
