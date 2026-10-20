import { useMemberApi } from "../app/providers";
import { formatGymDateTime } from "../dates/gymLocal";
import { queryKeys } from "../query/keys";
import { PagedScreen } from "../ui/PagedScreen";
import { Text } from "../ui/Text";

export function CheckInsScreen() {
  const api = useMemberApi();
  return (
    <PagedScreen
      title="Check-ins"
      queryKey={queryKeys.checkIns}
      load={(after) => api.checkIns(after)}
      empty="No check-ins are on file."
      renderItem={(item) => (
        <>
          <Text>
            {formatGymDateTime(item.check_in_at)} · {item.status}
          </Text>
          <Text>
            {item.method}
            {item.check_out_at
              ? ` · out ${formatGymDateTime(item.check_out_at)}`
              : " · still open"}
          </Text>
        </>
      )}
    />
  );
}
