import { useTx } from "../i18n/useTx";
import { useNavigation } from "@react-navigation/native";

import { useMemberApi } from "../app/providers";
import { formatGymDate, formatServerMoney } from "../dates/gymLocal";
import { queryKeys } from "../query/keys";
import { PagedScreen } from "../ui/PagedScreen";
import { Text } from "../ui/Text";
import { colors } from "../ui/theme";

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
      renderItem={(item) => (
        <>
          <Text style={{ fontWeight: "700" }}>
            {formatServerMoney(item.amount, item.currency)} · {tx(item.status)}
          </Text>
          <Text>{item.description}</Text>
          <Text style={{ color: colors.muted }}>
            {formatGymDate(item.business_date)}
            {item.receipt_number
              ? ` · ${tx("Receipt {number}", { number: item.receipt_number })}`
              : ""}
          </Text>
        </>
      )}
    />
  );
}
