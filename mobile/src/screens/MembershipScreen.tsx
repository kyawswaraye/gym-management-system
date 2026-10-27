import { useTx } from "../i18n/useTx";
import { useQuery } from "@tanstack/react-query";
import { RefreshControl } from "react-native";

import { useMemberApi, useOnline } from "../app/providers";
import { formatGymDate, formatServerMoney } from "../dates/gymLocal";
import { queryKeys } from "../query/keys";
import { Card } from "../ui/Card";
import { Fact } from "../ui/Fact";
import { NestedScreen } from "../ui/NestedScreen";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  OfflineBanner,
} from "../ui/StateView";
import { Text } from "../ui/Text";
import { colors } from "../ui/theme";

export function MembershipScreen() {
  const tx = useTx();
  const api = useMemberApi();
  const online = useOnline();
  const query = useQuery({
    queryKey: queryKeys.membership,
    queryFn: () => api.membership(),
  });
  const membership = query.data;
  return (
    <NestedScreen
      title={tx("Membership")}
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
      {query.isLoading ? (
        <LoadingState label={tx("Loading membership")} />
      ) : null}
      {query.isError ? (
        <ErrorState
          error={query.error}
          offline={!online}
          onRetry={() => query.refetch()}
        />
      ) : null}
      {query.isSuccess && !membership ? (
        <EmptyState message={tx("No current membership is on file.")} />
      ) : null}
      {membership ? (
        <Card>
          <Text style={{ fontSize: 20, fontWeight: "700" }}>
            {membership.plan_name}
          </Text>
          <Text style={{ color: colors.muted }}>{membership.plan_code}</Text>
          <Fact label={tx("Status")} value={tx(membership.status)} />
          <Text style={{ color: colors.muted }}>
            {formatGymDate(membership.start_date)} {tx("to")}{" "}
            {formatGymDate(membership.expiry_date)}
          </Text>
          <Fact
            label={tx("Price")}
            value={formatServerMoney(
              membership.final_amount,
              membership.currency,
            )}
          />
          <Fact label={tx("Payment")} value={tx(membership.payment_status)} />
        </Card>
      ) : null}
    </NestedScreen>
  );
}
