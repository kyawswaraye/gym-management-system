import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AssistantChat } from "@/features/assistant/chat";
import {
  confirmStep,
  loadConversations,
  loadStatus,
  rejectStep,
  sendTurn,
} from "@/features/assistant/client";

vi.mock("@/features/assistant/client", () => ({
  loadStatus: vi.fn(),
  loadConversations: vi.fn(),
  loadConversation: vi.fn(),
  sendTurn: vi.fn(),
  confirmStep: vi.fn(),
  rejectStep: vi.fn(),
  confirmationAmount: (pending: {
    approval: { amount: string } | null;
    fields: { name: string; value: string }[];
  }) =>
    pending.approval?.amount ??
    pending.fields.find((field) => field.name === "amount")?.value ??
    null,
}));

const status = vi.mocked(loadStatus);
const conversations = vi.mocked(loadConversations);
const turn = vi.mocked(sendTurn);
const confirm = vi.mocked(confirmStep);
const reject = vi.mocked(rejectStep);

describe("assistant chat", () => {
  beforeEach(() => {
    status.mockReset();
    conversations.mockReset();
    turn.mockReset();
    confirm.mockReset();
    reject.mockReset();
    status.mockResolvedValue({ available: true, reasons: [] });
    conversations.mockResolvedValue([]);
  });

  it("shows the stored amount and confirms without sending a new amount", async () => {
    turn.mockResolvedValue({
      conversation_id: "conversation-1",
      message: {
        id: "message-1",
        role: "assistant",
        body: "Please confirm the payment.",
        created_at: "2026-11-11T00:00:00Z",
      },
      pending_confirmation: {
        step_id: "step-1",
        goal_id: "goal-1",
        tool_name: "approve_payment",
        summary: "Approve a payment. Record pay-1, amount 40.00.",
        fields: [],
        approval: { record_id: "pay-1", amount: "40.00" },
      },
      tool_activity: [{ name: "approve_payment", status: "proposed" }],
    });
    confirm.mockResolvedValue({
      conversation_id: "conversation-1",
      message: {
        id: "message-2",
        role: "assistant",
        body: "The change is complete.",
        created_at: "2026-11-11T00:01:00Z",
      },
      pending_confirmation: null,
      tool_activity: [{ name: "approve_payment", status: "committed" }],
      outcome: "committed",
    });
    render(<AssistantChat membershipId="membership-1" />);
    fireEvent.change(screen.getByLabelText("Assistant"), {
      target: { value: "Approve the payment" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Amount 40.00")).toBeVisible();
    expect(screen.getByText("Record pay-1")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Confirm change" }));
    expect(confirm).toHaveBeenCalledWith("step-1");
    expect(await screen.findByText("The change is complete.")).toBeVisible();
    expect(screen.queryByText("Amount 40.00")).not.toBeInTheDocument();
  });

  it("shows an unavailable assistant", async () => {
    status.mockResolvedValue({ available: false, reasons: ["disabled"] });
    render(<AssistantChat membershipId="membership-1" />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The assistant is unavailable.",
    );
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(reject).not.toHaveBeenCalled();
  });
});
