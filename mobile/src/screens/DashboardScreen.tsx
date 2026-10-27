import { useLocale, useTx } from "../i18n/useTx";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Image, RefreshControl, View } from "react-native";

import { useMemberApi, useOnline } from "../app/providers";
import {
  formatGymDate,
  formatGymDateTime,
  formatServerMoney,
} from "../dates/gymLocal";
import { queryKeys } from "../query/keys";
import { BrandMark } from "../ui/BrandMark";
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
import { colors, elevation, radius } from "../ui/theme";

export function DashboardScreen() {
  const tx = useTx();
  const api = useMemberApi();
  const online = useOnline();
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: queryKeys.dashboard,
    queryFn: () => api.dashboard(),
  });
  const gym = query.data?.gym;
  return (
    <Screen
      title={tx("Home")}
      heading={
        <GymBrand
          hasLogo={gym?.has_logo ?? false}
          name={gym?.name ?? ""}
          pending={!gym}
        />
      }
      refreshControl={
        <RefreshControl
          refreshing={query.isRefetching}
          onRefresh={() => {
            void query.refetch();
            void queryClient.invalidateQueries({ queryKey: queryKeys.gymLogo });
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

function GymBrand({
  name,
  hasLogo,
  pending,
}: {
  name: string;
  hasLogo: boolean;
  pending: boolean;
}) {
  const tx = useTx();
  const locale = useLocale();
  const api = useMemberApi();
  const logo = useQuery({
    queryKey: queryKeys.gymLogo,
    enabled: hasLogo,
    queryFn: () => api.gymLogo(),
  });
  const waiting = pending || (hasLogo && logo.isLoading);
  const source = hasLogo && !logo.isError ? logo.data : null;
  return (
    <View style={{ alignItems: "center", gap: 16, paddingTop: 8 }}>
      <View
        style={{
          alignItems: "center",
          backgroundColor: colors.surface,
          borderColor: colors.border,
          borderRadius: 28,
          borderWidth: 1,
          justifyContent: "center",
          minHeight: 120,
          minWidth: 168,
          paddingHorizontal: 22,
          paddingVertical: 18,
          ...elevation,
        }}
      >
        {waiting ? (
          <View
            style={{
              backgroundColor: colors.primarySoft,
              borderRadius: 18,
              height: 64,
              width: 64,
            }}
          />
        ) : source ? (
          <Image
            accessibilityIgnoresInvertColors
            accessibilityLabel={tx("Gym logo")}
            accessibilityRole="image"
            resizeMode="contain"
            source={{ uri: source }}
            style={{ height: 72, width: 168 }}
          />
        ) : (
          <View style={{ alignItems: "center", gap: 8 }}>
            <BrandMark size={56} />
            <Text
              style={{
                fontSize: 18,
                fontWeight: "700",
                letterSpacing: 0.3,
              }}
            >
              {tx("GymPilot MM")}
            </Text>
          </View>
        )}
      </View>
      {name ? (
        <View style={{ alignItems: "center", gap: 10 }}>
          <Text
            accessibilityRole="header"
            style={{
              fontSize: 28,
              fontWeight: "700",
              letterSpacing: locale === "mm" ? 0 : -0.4,
              lineHeight: locale === "mm" ? 44 : 34,
              textAlign: "center",
            }}
          >
            {name}
          </Text>
          <View
            style={{
              backgroundColor: colors.accent,
              borderRadius: 2,
              height: 3,
              width: 32,
            }}
          />
        </View>
      ) : null}
    </View>
  );
}

function MemberAvatar({
  firstName,
  lastName,
  photoUrl,
}: {
  firstName: string;
  lastName: string;
  photoUrl?: string;
}) {
  const tx = useTx();
  const [failed, setFailed] = useState(false);
  const initials = `${Array.from(firstName.trim())[0] ?? ""}${Array.from(lastName.trim())[0] ?? ""}`;
  if (photoUrl && !failed) {
    return (
      <Image
        accessibilityLabel={tx("Profile photo")}
        onError={() => setFailed(true)}
        source={{ uri: photoUrl }}
        style={{
          backgroundColor: colors.primarySoft,
          borderRadius: 28,
          height: 56,
          width: 56,
        }}
      />
    );
  }
  return (
    <View
      accessibilityLabel={tx("Profile photo")}
      style={{
        alignItems: "center",
        backgroundColor: colors.primarySoft,
        borderRadius: 28,
        height: 56,
        justifyContent: "center",
        width: 56,
      }}
    >
      <Text style={{ color: colors.primary, fontWeight: "700" }}>
        {initials || "?"}
      </Text>
    </View>
  );
}

function DashboardBody({
  data,
}: {
  data: Awaited<ReturnType<ReturnType<typeof useMemberApi>["dashboard"]>>;
}) {
  const tx = useTx();
  const api = useMemberApi();
  const name = `${data.member.first_name} ${data.member.last_name}`.trim();
  const payment = data.payment_summary;
  const photoId = data.member.profile_photo_asset_id;
  const photo = useQuery({
    queryKey: queryKeys.photo(photoId ?? "none"),
    enabled: Boolean(photoId),
    queryFn: () => api.photoDownloadUrl(photoId ?? ""),
  });
  const days =
    data.days_remaining === null
      ? tx("Unavailable")
      : String(data.days_remaining);
  return (
    <>
      <Card>
        <View style={{ alignItems: "center", flexDirection: "row", gap: 14 }}>
          <MemberAvatar
            firstName={data.member.first_name}
            lastName={data.member.last_name}
            photoUrl={photo.data?.url}
          />
          <View style={{ flex: 1, gap: 2 }}>
            <Text
              accessibilityLabel={tx("Signed in as {name}", { name })}
              style={{ fontSize: 20, fontWeight: "700" }}
            >
              {name}
            </Text>
            <Text style={{ color: colors.muted, fontSize: 14 }}>
              {tx("Member number")} {data.member.member_number}
            </Text>
          </View>
        </View>
        {data.current_membership ? (
          <Text>
            {data.current_membership.plan_name} {tx("until")}{" "}
            {formatGymDate(data.current_membership.expiry_date)}
          </Text>
        ) : (
          <EmptyState message={tx("No current membership is on file.")} />
        )}
        <View
          style={{
            backgroundColor: colors.primarySoft,
            borderRadius: radius.sm,
            gap: 2,
            paddingHorizontal: 16,
            paddingVertical: 14,
          }}
        >
          <Text
            style={{
              color: colors.primary,
              fontSize: 32,
              fontWeight: "700",
              lineHeight: 38,
            }}
          >
            {days}
          </Text>
          <Text style={{ color: colors.primary, fontWeight: "600" }}>
            {tx("Days remaining:")}
          </Text>
        </View>
        <Text style={{ color: colors.muted }}>
          {data.is_pt_client
            ? tx("Personal training client")
            : tx("No personal training assignment")}
        </Text>
      </Card>
      <Card>
        <Text
          accessibilityRole="header"
          style={{ fontSize: 18, fontWeight: "700" }}
        >
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
        <Text
          accessibilityRole="header"
          style={{ fontSize: 18, fontWeight: "700" }}
        >
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
