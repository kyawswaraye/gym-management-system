import { useQuery } from "@tanstack/react-query";
import { RefreshControl } from "react-native";

import { useMemberApi, useOnline } from "../app/providers";
import { formatGymDate, formatServerMoney } from "../dates/gymLocal";
import { queryKeys } from "../query/keys";
import { Screen } from "../ui/Screen";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  OfflineBanner,
} from "../ui/StateView";
import { Text } from "../ui/Text";

export function MembershipScreen() {
  const api = useMemberApi();
  const online = useOnline();
  const query = useQuery({
    queryKey: queryKeys.membership,
    queryFn: () => api.membership(),
  });
  const membership = query.data;
  return (
    <Screen
      title="Membership"
      refreshControl={
        <RefreshControl
          refreshing={query.isRefetching}
          onRefresh={() => {
            void query.refetch();
          }}
        />
      }
    >
      <OfflineBanner visible={!online} />
      {query.isLoading ? <LoadingState label="Loading membership" /> : null}
      {query.isError ? (
        <ErrorState
          error={query.error}
          offline={!online}
          onRetry={() => query.refetch()}
        />
      ) : null}
      {query.isSuccess && !membership ? (
        <EmptyState message="No current membership is on file." />
      ) : null}
      {membership ? (
        <>
          <Text>
            {membership.plan_name} ({membership.plan_code})
          </Text>
          <Text>Status {membership.status}</Text>
          <Text>
            {formatGymDate(membership.start_date)} to{" "}
            {formatGymDate(membership.expiry_date)}
          </Text>
          <Text>
            Price{" "}
            {formatServerMoney(membership.final_amount, membership.currency)}
          </Text>
          <Text>Payment {membership.payment_status}</Text>
        </>
      ) : null}
    </Screen>
  );
}
