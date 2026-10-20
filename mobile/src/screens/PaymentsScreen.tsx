import { useMemberApi } from "../app/providers";
import { formatGymDate, formatServerMoney } from "../dates/gymLocal";
import { queryKeys } from "../query/keys";
import { PagedScreen } from "../ui/PagedScreen";
import { Text } from "../ui/Text";

export function PaymentsScreen() {
  const api = useMemberApi();
  return (
    <PagedScreen
      title="Payments"
      queryKey={queryKeys.payments}
      load={(after) => api.payments(after)}
      empty="No payments are on file."
      renderItem={(item) => (
        <>
          <Text>
            {formatServerMoney(item.amount, item.currency)} · {item.status}
          </Text>
          <Text>{item.description}</Text>
          <Text>
            {formatGymDate(item.business_date)}
            {item.receipt_number ? ` · Receipt ${item.receipt_number}` : ""}
          </Text>
        </>
      )}
    />
  );
}
