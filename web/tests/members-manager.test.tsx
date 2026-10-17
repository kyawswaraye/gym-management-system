import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { listMembers, operationMutation } from "@/features/operations/client";
import { MembersManager } from "@/features/operations/members-manager";
import type { Member, MemberListPage, MemberListQuery } from "@/types/api";

vi.mock("@/features/operations/client", () => ({
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
    fireEvent.change(screen.getByLabelText("Filter by status"), {
      target: { value: "ACTIVE" },
    });
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

    fireEvent.change(screen.getByLabelText("Filter by status"), {
      target: { value: "ACTIVE" },
    });
    await waitFor(() =>
      expect(listMembers).toHaveBeenLastCalledWith({
        limit: 25,
        status: "ACTIVE",
      }),
    );
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
        first_name: "Grace",
        last_name: "Hopper",
        phone: "777",
        email: "grace@example.test",
        join_date: "2026-10-17",
      }),
    );
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
});
