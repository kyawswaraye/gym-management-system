import {
  operationGet,
  operationList,
  operationMutation,
} from "@/features/operations/client";

export type AssistantStatus = {
  available: boolean;
  reasons: string[];
};

export type AssistantMessage = {
  id: string;
  role: string;
  body: string;
  created_at: string;
};

export type ConfirmationField = {
  name: string;
  value: string;
};

export type PendingConfirmation = {
  step_id: string;
  goal_id: string;
  tool_name: string;
  summary: string;
  fields: ConfirmationField[];
  approval: { record_id: string; amount: string } | null;
};

export type ToolActivity = {
  name: string;
  status: string;
};

export type TurnResult = {
  conversation_id: string;
  message: AssistantMessage;
  pending_confirmation: PendingConfirmation | null;
  tool_activity: ToolActivity[];
  outcome?: string;
};

export type ConversationSummary = {
  id: string;
  status: string;
  title: string;
  created_by_membership_id: string;
  created_at: string;
};

export type ConversationDetail = ConversationSummary & {
  messages: AssistantMessage[];
  pending_confirmation: PendingConfirmation | null;
};

export function loadStatus(): Promise<AssistantStatus> {
  return operationGet<AssistantStatus>("assistant/status/");
}

export function loadConversations(): Promise<ConversationSummary[]> {
  return operationList<ConversationSummary>("assistant/conversations/").then(
    (page) => page.data,
  );
}

export function loadConversation(id: string): Promise<ConversationDetail> {
  return operationGet<ConversationDetail>(`assistant/conversations/${id}/`);
}

export function sendTurn(
  message: string,
  conversationId: string | null,
): Promise<TurnResult> {
  return operationMutation<TurnResult>("assistant/turns/", {
    message,
    ...(conversationId ? { conversation_id: conversationId } : {}),
  });
}

export function confirmStep(stepId: string): Promise<TurnResult> {
  return operationMutation<TurnResult>(
    `assistant/confirmations/${stepId}/confirm/`,
    { confirmed: true },
  );
}

export function rejectStep(stepId: string): Promise<TurnResult> {
  return operationMutation<TurnResult>(
    `assistant/confirmations/${stepId}/reject/`,
    {},
  );
}

export function confirmationAmount(
  pending: PendingConfirmation,
): string | null {
  if (pending.approval?.amount) return pending.approval.amount;
  const field = pending.fields.find(
    (item) => item.name === "amount" || item.name === "price",
  );
  return field?.value || null;
}
