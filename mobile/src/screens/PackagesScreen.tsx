import { useMemberApi } from "../app/providers";
import { formatGymDate, formatRemainingSessions } from "../dates/gymLocal";
import { queryKeys } from "../query/keys";
import { PagedScreen } from "../ui/PagedScreen";
import { Text } from "../ui/Text";

export function PackagesScreen() {
  const api = useMemberApi();
  return (
    <PagedScreen
      title="Packages"
      queryKey={queryKeys.packages}
      load={(after) => api.servicePackages(after)}
      empty="No service packages are on file."
      renderItem={(item) => (
        <>
          <Text>
            {item.package_name} · {item.package_type}
          </Text>
          <Text>Status {item.status}</Text>
          <Text>
            Remaining sessions{" "}
            {formatRemainingSessions(item.remaining_sessions)}
          </Text>
          <Text>
            {formatGymDate(item.valid_from)} to{" "}
            {formatGymDate(item.valid_until)}
          </Text>
          {item.complimentary ? <Text>Complimentary</Text> : null}
        </>
      )}
    />
  );
}
