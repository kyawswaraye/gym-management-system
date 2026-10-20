import { useQuery } from "@tanstack/react-query";
import { RefreshControl } from "react-native";

import { useMemberApi, useOnline } from "../app/providers";
import {
  formatGymDate,
  formatGymDateTime,
  formatServerMoney,
} from "../dates/gymLocal";
import { queryKeys } from "../query/keys";
import { Screen } from "../ui/Screen";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  OfflineBanner,
} from "../ui/StateView";
import { Text } from "../ui/Text";

export function DashboardScreen() {
  const api = useMemberApi();
  const online = useOnline();
  const query = useQuery({
    queryKey: queryKeys.dashboard,
    queryFn: () => api.dashboard(),
  });
  return (
    <Screen
      title="Home"
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
        <LoadingState label="Loading your gym summary" />
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
  const name = `${data.member.first_name} ${data.member.last_name}`.trim();
  const payment = data.payment_summary;
  return (
    <>
      <Text accessibilityLabel={`Signed in as ${name}`}>{name}</Text>
      <Text>Member number {data.member.member_number}</Text>
      {data.current_membership ? (
        <Text>
          {data.current_membership.plan_name} until{" "}
          {formatGymDate(data.current_membership.expiry_date)}
        </Text>
      ) : (
        <EmptyState message="No current membership is on file." />
      )}
      <Text>
        Days remaining:{" "}
        {data.days_remaining === null
          ? "Unavailable"
          : String(data.days_remaining)}
      </Text>
      <Text>
        {data.is_pt_client
          ? "Personal training client"
          : "No personal training assignment"}
      </Text>
      <Text accessibilityRole="header" style={{ fontWeight: "700" }}>
        Payments
      </Text>
      {payment.available ? (
        <>
          <Text>
            Charged {formatServerMoney(payment.total_charged, payment.currency)}
          </Text>
          <Text>
            Refunded{" "}
            {formatServerMoney(payment.total_refunded, payment.currency)}
          </Text>
          <Text>
            Net {formatServerMoney(payment.net_paid, payment.currency)}
          </Text>
          <Text>
            Outstanding{" "}
            {formatServerMoney(
              payment.outstanding_membership_balance,
              payment.currency,
            )}
          </Text>
        </>
      ) : (
        <Text>Payment totals are unavailable.</Text>
      )}
      <Text accessibilityRole="header" style={{ fontWeight: "700" }}>
        Check-in
      </Text>
      {data.check_in_summary.available ? (
        <Text>
          {data.check_in_summary.currently_checked_in
            ? "You are checked in."
            : "You are not checked in."}
          {data.check_in_summary.last_check_in_at
            ? ` Last visit ${formatGymDateTime(data.check_in_summary.last_check_in_at)}.`
            : ""}
        </Text>
      ) : (
        <Text>Check-in status is unavailable.</Text>
      )}
    </>
  );
}
