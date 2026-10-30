import { useTx } from "../i18n/useTx";
import { useNavigation } from "@react-navigation/native";

import { useMemberApi } from "../app/providers";
import { formatGymDate, formatServerMoney } from "../dates/gymLocal";
import { queryKeys } from "../query/keys";
import { PagedScreen } from "../ui/PagedScreen";
import { Text } from "../ui/Text";
import { colors } from "../ui/theme";

const STATUS_LABEL: Record<string, string> = {
  DRAFT: "Draft — not finished",
  PENDING_APPROVAL: "Waiting for gym approval",
  POSTED: "Paid",
  REJECTED: "Rejected by gym",
  CANCELLED: "Cancelled",
  VOID: "Voided",
};

const PURPOSE_LABEL: Record<string, string> = {
  MEMBERSHIP: "Membership",
  PT: "Personal training",
  PRODUCT: "Shop purchase",
  CLASS: "Class package",
  OTHER: "Other payment",
};

const METHOD_LABEL: Record<string, string> = {
  CASH: "Cash",
  BANK_TRANSFER: "Bank transfer",
  CARD: "Card",
  MOBILE_PAYMENT: "Mobile payment",
  OTHER: "Other method",
};

const ENTRY_LABEL: Record<string, string> = {
  CHARGE: "Charge",
  REFUND: "Refund",
  REVERSAL: "Reversal",
};

export function PaymentsScreen() {
  const tx = useTx();
  const api = useMemberApi();
  const navigation = useNavigation();
  return (
    <PagedScreen
      onBack={() => navigation.goBack()}
      title={tx("Payments")}
      queryKey={queryKeys.payments}
      load={(after) => api.payments(after)}
      empty="No payments are on file."
      lead={
        <Text style={{ color: colors.muted }}>
          {tx(
            "These are payments your gym recorded for you. Paid means the gym posted the payment. Waiting means staff still need to approve it.",
          )}
        </Text>
      }
      renderItem={(item) => {
        const statusKey = STATUS_LABEL[item.status] ?? item.status;
        const purposeKey = PURPOSE_LABEL[item.purpose] ?? item.purpose;
        const methodKey = METHOD_LABEL[item.method] ?? item.method;
        const entryKey = ENTRY_LABEL[item.entry_type] ?? item.entry_type;
        const amountPrefix =
          item.entry_type === "REFUND" || item.entry_type === "REVERSAL"
            ? "−"
            : "";
        return (
          <>
            <Text style={{ fontWeight: "700" }}>
              {amountPrefix}
              {formatServerMoney(item.amount, item.currency)}
            </Text>
            <Text>
              {tx(purposeKey)}
              {item.description ? ` · ${item.description}` : ""}
            </Text>
            <Text style={{ color: colors.muted }}>
              {tx(statusKey)} · {tx(entryKey)} · {tx(methodKey)}
            </Text>
            <Text style={{ color: colors.muted }}>
              {formatGymDate(item.business_date)}
              {item.receipt_number
                ? ` · ${tx("Receipt {number}", { number: item.receipt_number })}`
                : ""}
            </Text>
          </>
        );
      }}
    />
  );
}
