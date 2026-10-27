import { useTx } from "../i18n/useTx";
import { useQuery } from "@tanstack/react-query";
import { RefreshControl } from "react-native";

import { useMemberApi, useOnline } from "../app/providers";
import {
  formatGymDate,
  formatGymDateTime,
  formatServerMoney,
} from "../dates/gymLocal";
import { queryKeys } from "../query/keys";
import { Card } from "../ui/Card";
import { Fact } from "../ui/Fact";
import { Screen } from "../ui/Screen";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  OfflineBanner,
} from "../ui/StateView";
import { Text } from "../ui/Text";
import { colors } from "../ui/theme";

export function DashboardScreen() {
  const tx = useTx();
  const api = useMemberApi();
  const online = useOnline();
  const query = useQuery({
    queryKey: queryKeys.dashboard,
    queryFn: () => api.dashboard(),
  });
  return (
    <Screen
      title={tx("Home")}
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
        <LoadingState label={tx("Loading your gym summary")} />
      ) : null}
      {query.isError ? (
        <ErrorState
          error={query.error}
          offline={!online}
          onRetry={() => query.refetch()}
        />
      ) : null}
      {query.data ? <DashboardBody data={query.data} /> : null}
    </Screen>
  );
}

function DashboardBody({
  data,
}: {
  data: Awaited<ReturnType<ReturnType<typeof useMemberApi>["dashboard"]>>;
}) {
  const tx = useTx();
  const name = `${data.member.first_name} ${data.member.last_name}`.trim();
  const payment = data.payment_summary;
  return (
    <>
      <Card>
        <Text
          accessibilityLabel={tx("Signed in as {name}", { name })}
          style={{ fontSize: 22, fontWeight: "700" }}
        >
          {name}
        </Text>
        <Text style={{ color: colors.muted }}>
          {tx("Member number")} {data.member.member_number}
        </Text>
        {data.current_membership ? (
          <Text>
            {data.current_membership.plan_name} {tx("until")}{" "}
            {formatGymDate(data.current_membership.expiry_date)}
          </Text>
        ) : (
          <EmptyState message={tx("No current membership is on file.")} />
        )}
        <Text
          style={{
            backgroundColor: colors.primarySoft,
            borderRadius: 12,
            color: colors.primary,
            fontWeight: "700",
            overflow: "hidden",
            paddingHorizontal: 12,
            paddingVertical: 10,
          }}
        >
          {tx("Days remaining:")}{" "}
          {data.days_remaining === null
            ? tx("Unavailable")
            : String(data.days_remaining)}
        </Text>
        <Text style={{ color: colors.muted }}>
          {data.is_pt_client
            ? tx("Personal training client")
            : tx("No personal training assignment")}
        </Text>
      </Card>
      <Card>
        <Text accessibilityRole="header" style={{ fontWeight: "700" }}>
          {tx("Payments")}
        </Text>
        {payment.available ? (
          <>
            <Fact
              label={tx("Charged")}
              value={formatServerMoney(payment.total_charged, payment.currency)}
            />
            <Fact
              label={tx("Refunded")}
              value={formatServerMoney(
                payment.total_refunded,
                payment.currency,
              )}
            />
            <Fact
              label={tx("Net")}
              value={formatServerMoney(payment.net_paid, payment.currency)}
            />
            <Fact
              label={tx("Outstanding")}
              value={formatServerMoney(
                payment.outstanding_membership_balance,
                payment.currency,
              )}
            />
          </>
        ) : (
          <Text>{tx("Payment totals are unavailable.")}</Text>
        )}
      </Card>
      <Card>
        <Text accessibilityRole="header" style={{ fontWeight: "700" }}>
          {tx("Check-in")}
        </Text>
        {data.check_in_summary.available ? (
          <Text>
            {data.check_in_summary.currently_checked_in
              ? tx("You are checked in.")
              : tx("You are not checked in.")}
            {data.check_in_summary.last_check_in_at
              ? ` ${tx("Last visit {time}.", { time: formatGymDateTime(data.check_in_summary.last_check_in_at) })}`
              : ""}
          </Text>
        ) : (
          <Text>{tx("Check-in status is unavailable.")}</Text>
        )}
      </Card>
    </>
  );
}
