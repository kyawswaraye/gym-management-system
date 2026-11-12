import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AssistantChat } from "@/features/assistant/chat";
import {
  confirmStep,
  loadConversation,
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
const conversation = vi.mocked(loadConversation);
const turn = vi.mocked(sendTurn);
const confirm = vi.mocked(confirmStep);
const reject = vi.mocked(rejectStep);

describe("assistant chat", () => {
  beforeEach(() => {
    status.mockReset();
    conversations.mockReset();
    conversation.mockReset();
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

  it("shows one stored sale line and confirms without sending the line", async () => {
    turn.mockResolvedValue({
      conversation_id: "conversation-1",
      message: {
        id: "message-1",
        role: "assistant",
        body: "Please confirm the sale.",
        created_at: "2026-11-11T00:00:00Z",
      },
      pending_confirmation: {
        step_id: "step-sale",
        goal_id: "goal-1",
        tool_name: "create_retail_sale",
        summary: "Create a retail sale draft.",
        fields: [
          { name: "amount", value: "30.00" },
          { name: "lines.0.product", value: "Spring Water (WATER-B)" },
          { name: "lines.0.quantity", value: "2" },
          { name: "lines.0.discount", value: "5.00" },
        ],
        approval: null,
      },
      tool_activity: [],
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
      tool_activity: [],
      outcome: "committed",
    });
    render(<AssistantChat membershipId="membership-1" />);
    fireEvent.change(screen.getByLabelText("Assistant"), {
      target: { value: "Sell two bottles" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Spring Water (WATER-B)")).toBeVisible();
    expect(screen.getByText("Quantity 2")).toBeVisible();
    expect(screen.getByText("Discount 5.00")).toBeVisible();
    expect(screen.getByText("Amount 30.00")).toBeVisible();
    expect(screen.queryByText(/aaaaaaaa/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm change" }));
    expect(confirm).toHaveBeenCalledWith("step-sale");
    expect(confirm).toHaveBeenCalledTimes(1);
  });

  it("shows every stored sale line with its name and sku", async () => {
    turn.mockResolvedValue({
      conversation_id: "conversation-1",
      message: {
        id: "message-1",
        role: "assistant",
        body: "Please confirm the sale.",
        created_at: "2026-11-11T00:00:00Z",
      },
      pending_confirmation: {
        step_id: "step-lines",
        goal_id: "goal-1",
        tool_name: "create_retail_sale",
        summary: "Create a retail sale draft.",
        fields: [
          { name: "lines.0.product", value: "Spring Water (WATER-B)" },
          { name: "lines.0.quantity", value: "2" },
          { name: "lines.0.discount", value: "1.00" },
          { name: "lines.1.product", value: "Gym Towel (TOWEL-1)" },
          { name: "lines.1.quantity", value: "1" },
        ],
        approval: null,
      },
      tool_activity: [],
    });
    reject.mockResolvedValue({
      conversation_id: "conversation-1",
      message: {
        id: "message-2",
        role: "assistant",
        body: "The change was rejected.",
        created_at: "2026-11-11T00:01:00Z",
      },
      pending_confirmation: null,
      tool_activity: [],
      outcome: "cancelled",
    });
    render(<AssistantChat membershipId="membership-1" />);
    fireEvent.change(screen.getByLabelText("Assistant"), {
      target: { value: "Sell water and a towel" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Spring Water (WATER-B)")).toBeVisible();
    expect(screen.getByText("Gym Towel (TOWEL-1)")).toBeVisible();
    expect(screen.getByText("Quantity 2")).toBeVisible();
    expect(screen.getByText("Quantity 1")).toBeVisible();
    expect(screen.getByText("Discount 1.00")).toBeVisible();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Reject change" }));
    expect(reject).toHaveBeenCalledWith("step-lines");
    expect(confirm).not.toHaveBeenCalled();
  });

  it("shows an unavailable product when a line has no stored name", async () => {
    turn.mockResolvedValue({
      conversation_id: "conversation-1",
      message: {
        id: "message-1",
        role: "assistant",
        body: "Please confirm the sale.",
        created_at: "2026-11-11T00:00:00Z",
      },
      pending_confirmation: {
        step_id: "step-missing",
        goal_id: "goal-1",
        tool_name: "create_retail_sale",
        summary: "Create a retail sale draft.",
        fields: [{ name: "lines.0.product", value: "Product unavailable" }],
        approval: null,
      },
      tool_activity: [],
    });
    render(<AssistantChat membershipId="membership-1" />);
    fireEvent.change(screen.getByLabelText("Assistant"), {
      target: { value: "Sell something" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Product unavailable")).toBeVisible();
    expect(screen.queryByText(/Quantity/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Discount/)).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Confirm change" }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Reject change" })).toBeVisible();
  });

  it("keeps each sent message when another one is sent", async () => {
    const errors: unknown[][] = [];
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation((...args) => {
        errors.push(args);
      });
    turn
      .mockResolvedValueOnce({
        conversation_id: "conversation-1",
        message: {
          id: "reply-1",
          role: "assistant",
          body: "First reply",
          created_at: "2026-11-11T00:00:00Z",
        },
        pending_confirmation: null,
        tool_activity: [],
      })
      .mockResolvedValueOnce({
        conversation_id: "conversation-1",
        message: {
          id: "reply-2",
          role: "assistant",
          body: "Second reply",
          created_at: "2026-11-11T00:01:00Z",
        },
        pending_confirmation: null,
        tool_activity: [],
      });
    render(<AssistantChat membershipId="membership-1" />);
    const composer = screen.getByLabelText("Assistant");
    fireEvent.change(composer, { target: { value: "First question" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("First reply")).toBeVisible();
    fireEvent.change(composer, { target: { value: "Second question" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Second reply")).toBeVisible();
    const log = screen.getByRole("log");
    expect(log).toHaveTextContent("First question");
    expect(log).toHaveTextContent("Second question");
    expect(errors.join("\n")).not.toContain("same key");
    consoleError.mockRestore();
  });

  it("scrolls the transcript to the latest message", async () => {
    turn.mockResolvedValue({
      conversation_id: "conversation-1",
      message: {
        id: "reply-1",
        role: "assistant",
        body: "First reply",
        created_at: "2026-11-11T00:00:00Z",
      },
      pending_confirmation: null,
      tool_activity: [],
    });
    render(<AssistantChat membershipId="membership-1" />);
    const log = screen.getByRole("log");
    Object.defineProperty(log, "scrollHeight", {
      configurable: true,
      value: 900,
    });
    fireEvent.change(screen.getByLabelText("Assistant"), {
      target: { value: "Hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("First reply")).toBeVisible();
    expect(log.scrollTop).toBe(900);
  });

  it("reopens the latest conversation after refresh", async () => {
    conversations.mockResolvedValue([
      {
        id: "conversation-new",
        status: "open",
        title: "Newer",
        created_by_membership_id: "membership-1",
        created_at: "2026-11-11T00:02:00Z",
      },
    ]);
    conversation.mockResolvedValue({
      id: "conversation-new",
      status: "open",
      title: "Newer",
      created_by_membership_id: "membership-1",
      created_at: "2026-11-11T00:02:00Z",
      messages: [
        {
          id: "message-1",
          role: "staff",
          body: "Saved question",
          created_at: "2026-11-11T00:02:00Z",
        },
        {
          id: "message-2",
          role: "assistant",
          body: "Saved reply",
          created_at: "2026-11-11T00:02:01Z",
        },
      ],
      pending_confirmation: null,
    });
    render(<AssistantChat membershipId="membership-1" />);
    expect(await screen.findByText("Saved reply")).toBeVisible();
    expect(screen.getByRole("log")).toHaveTextContent("Saved question");
    expect(conversation).toHaveBeenCalledWith("conversation-new");
  });

  it("keeps past chats behind the history button", async () => {
    conversations.mockResolvedValue([
      {
        id: "conversation-old",
        status: "open",
        title: "Older chat",
        created_by_membership_id: "membership-1",
        created_at: "2026-11-11T00:01:00Z",
      },
    ]);
    conversation.mockResolvedValue({
      id: "conversation-old",
      status: "open",
      title: "Older chat",
      created_by_membership_id: "membership-1",
      created_at: "2026-11-11T00:01:00Z",
      messages: [
        {
          id: "message-1",
          role: "assistant",
          body: "Saved reply",
          created_at: "2026-11-11T00:01:01Z",
        },
      ],
      pending_confirmation: null,
    });
    render(<AssistantChat membershipId="membership-1" />);
    expect(await screen.findByText("Saved reply")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Older chat" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "History" }));
    expect(screen.getByRole("button", { name: "Older chat" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Close history" }));
    expect(
      screen.queryByRole("button", { name: "Older chat" }),
    ).not.toBeInTheDocument();
  });

  it("does not show a raw tool error", async () => {
    turn.mockResolvedValue({
      conversation_id: "conversation-1",
      message: {
        id: "reply-1",
        role: "assistant",
        body: "PT package မရှိသေးပါ။",
        created_at: "2026-11-11T00:00:00Z",
      },
      pending_confirmation: null,
      tool_activity: [{ name: "create_pt_booking", status: "error" }],
    });
    render(<AssistantChat membershipId="membership-1" />);
    fireEvent.change(screen.getByLabelText("Assistant"), {
      target: { value: "Aung Aung အတွက် PT booking လုပ်ပေးပါ" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("PT package မရှိသေးပါ။")).toBeVisible();
    expect(
      screen.queryByText(/create_pt_booking error/),
    ).not.toBeInTheDocument();
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
