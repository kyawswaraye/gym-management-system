import { operationMutation } from "@/features/operations/client";
import type {
  Membership,
  MembershipPlan,
  MembershipPlanUpdate,
  MembershipUpdate,
} from "@/types/api";

export function updateMembershipPlan(
  planId: string,
  body: MembershipPlanUpdate,
): Promise<MembershipPlan> {
  return operationMutation<MembershipPlan>(
    `membership-plans/${planId}/`,
    body,
    "PATCH",
  );
}

export function updateMembership(
  membershipId: string,
  body: MembershipUpdate,
): Promise<Membership> {
  return operationMutation<Membership>(
    `memberships/${membershipId}/`,
    body,
    "PATCH",
  );
}
