import type {
  PlatformSubscription,
  PlatformSubscriptionUpdate,
} from "@/types/api";

const PRICE_PATTERN = /^(?:0|[1-9]\d{0,15})(?:\.\d{1,2})?$/;
const INTERVAL_PATTERN = /^[A-Z][A-Z0-9_]{0,19}$/;

const INTERVAL_ORDER = ["TRIAL", "MONTHLY", "QUARTERLY", "YEARLY", "LIFETIME"];
const CURRENCY_ORDER = ["THB", "MMK"];

export function compareSubscriptionPlans(
  left: PlatformSubscription,
  right: PlatformSubscription,
): number {
  const interval =
    (INTERVAL_ORDER.indexOf(left.billing_interval) + 1 || 99) -
    (INTERVAL_ORDER.indexOf(right.billing_interval) + 1 || 99);
  if (interval !== 0) return interval;
  const currency =
    (CURRENCY_ORDER.indexOf(left.currency) + 1 || 99) -
    (CURRENCY_ORDER.indexOf(right.currency) + 1 || 99);
  if (currency !== 0) return currency;
  return left.name.localeCompare(right.name);
}

export type SubscriptionDraft = {
  name: string;
  price: string;
  billingInterval: string;
  status: string;
};

export type SubscriptionUpdateResult =
  | { ok: true; body: PlatformSubscriptionUpdate }
  | { ok: false; message: string };

export function normalizePlanPrice(value: string): string | null {
  const trimmed = value.trim();
  if (!PRICE_PATTERN.test(trimmed)) return null;
  const [whole, fraction = ""] = trimmed.split(".");
  return `${whole}.${fraction.padEnd(2, "0")}`;
}

export function buildSubscriptionUpdate(
  current: PlatformSubscription,
  draft: SubscriptionDraft,
): SubscriptionUpdateResult {
  const name = draft.name.trim();
  if (!name || name.length > 100) {
    return { ok: false, message: "Enter a plan name up to 100 characters." };
  }
  const price = normalizePlanPrice(draft.price);
  if (!price) {
    return {
      ok: false,
      message: "Enter a nonnegative price with up to 2 decimal places.",
    };
  }
  const billingInterval = draft.billingInterval.trim();
  if (!INTERVAL_PATTERN.test(billingInterval)) {
    return { ok: false, message: "Enter an uppercase billing interval." };
  }
  if (draft.status !== "ACTIVE" && draft.status !== "RETIRED") {
    return { ok: false, message: "Choose ACTIVE or RETIRED." };
  }

  const body: PlatformSubscriptionUpdate = {};
  if (name !== current.name) body.name = name;
  if (price !== current.price) body.price = price;
  if (billingInterval !== current.billing_interval) {
    body.billing_interval = billingInterval;
  }
  if (draft.status !== current.status) body.status = draft.status;
  if (Object.keys(body).length === 0) {
    return { ok: false, message: "Change at least one field." };
  }
  return { ok: true, body };
}
