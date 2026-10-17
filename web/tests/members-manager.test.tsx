import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { operationGet, operationMutation } from "@/features/operations/client";
import { MembersManager } from "@/features/operations/members-manager";
import type { Member } from "@/types/api";

vi.mock("@/features/operations/client", () => ({
  operationGet: vi.fn(),
  operationMutation: vi.fn(),
}));

const MEMBER_ID = "11111111-1111-4111-8111-111111111111";

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

describe("Members manager", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(operationGet).mockResolvedValue([member()]);
    vi.mocked(operationMutation).mockResolvedValue(member());
  });

  it("renders the published first and last name", async () => {
    render(<MembersManager canCreate defaultDate="2026-10-17" />);

    expect(
      await screen.findByRole("link", { name: "Ada Lovelace" }),
    ).toHaveAttribute("href", `/members/${MEMBER_ID}`);
    expect(screen.getByText("MEM-000001")).toBeInTheDocument();
    expect(screen.getByText("555")).toBeInTheDocument();
    expect(screen.getByText("2026-01-01")).toBeInTheDocument();
    expect(screen.getByText("ACTIVE")).toBeInTheDocument();
    expect(screen.queryByText("ada lovelace")).not.toBeInTheDocument();
  });

  it("creates a member with the published name fields", async () => {
    render(<MembersManager canCreate defaultDate="2026-10-17" />);
    await screen.findByRole("link", { name: "Ada Lovelace" });

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
  });
});
