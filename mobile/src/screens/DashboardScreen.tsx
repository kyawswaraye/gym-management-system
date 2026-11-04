import { useLocale, useTx } from "../i18n/useTx";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Animated, Image, RefreshControl, View } from "react-native";

import { useReducedMotion } from "../a11y/motion";
import { useMemberApi, useOnline } from "../app/providers";
import {
  formatGymDate,
  formatGymDateTime,
  formatServerMoney,
} from "../dates/gymLocal";
import { queryKeys } from "../query/keys";
import { BrandMark } from "../ui/BrandMark";
import { GymLogoImage } from "../ui/GymLogoImage";
import { Card } from "../ui/Card";
import { Fact } from "../ui/Fact";
import { PhotoBand } from "../ui/PhotoBand";
import { Screen } from "../ui/Screen";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  OfflineBanner,
} from "../ui/StateView";
import { Text } from "../ui/Text";
import { colors, radius, space } from "../ui/theme";

const logoSize = 56;
const stripHeight = 148;

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
          name={gym?.name?.trim() ?? ""}
          pending={query.isLoading}
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
  const media = hasLogo && logo.isSuccess ? logo.data : null;
  const waiting = pending || (hasLogo && logo.isLoading);
  return (
    <View style={{ alignSelf: "stretch", gap: space.sm }}>
      <PhotoBand
        accessibilityLabel={tx("Gym studio")}
        animate
        height={stripHeight}
        rounded
        scrim="bottom"
        source={require("../assets/studio-strip.jpg")}
      >
        <View
          style={{
            alignItems: "center",
            flexDirection: "row",
            gap: 12,
            maxWidth: "100%",
          }}
        >
          {media ? (
            <View
              style={{
                backgroundColor: colors.surface,
                borderRadius: 16,
                padding: 4,
              }}
            >
              <GymLogoImage
                fallback={<DefaultMark />}
                label={tx("Gym logo")}
                media={media}
                size={logoSize}
              />
            </View>
          ) : waiting ? (
            <View
              style={{
                backgroundColor: colors.primarySoft,
                borderRadius: 16,
                height: logoSize,
                width: logoSize,
              }}
            />
          ) : (
            <View
              style={{
                backgroundColor: colors.surface,
                borderRadius: 16,
                padding: 4,
              }}
            >
              <DefaultMark />
            </View>
          )}
          {name ? (
            <Text
              accessibilityRole="header"
              numberOfLines={3}
              style={{
                color: colors.onPrimary,
                flexShrink: 1,
                fontSize: 22,
                fontWeight: "800",
                letterSpacing: locale === "mm" ? 0 : -0.3,
                lineHeight: locale === "mm" ? 34 : 28,
              }}
            >
              {name}
            </Text>
          ) : null}
        </View>
      </PhotoBand>
    </View>
  );
}

function DefaultMark() {
  const tx = useTx();
  return (
    <View accessibilityLabel={tx("GymPilot MM")} accessibilityRole="image">
      <BrandMark size={logoSize} />
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

function DaysCallout({ days }: { days: string }) {
  const tx = useTx();
  const reduced = useReducedMotion();
  const scale = useRef(new Animated.Value(reduced ? 1 : 0.92)).current;
  const opacity = useRef(new Animated.Value(reduced ? 1 : 0)).current;

  useEffect(() => {
    if (reduced) {
      scale.setValue(1);
      opacity.setValue(1);
      return;
    }
    Animated.parallel([
      Animated.spring(scale, {
        friction: 7,
        tension: 80,
        toValue: 1,
        useNativeDriver: true,
      }),
      Animated.timing(opacity, {
        toValue: 1,
        duration: 320,
        useNativeDriver: true,
      }),
    ]).start();
  }, [opacity, reduced, scale]);

  return (
    <Animated.View
      style={{
        backgroundColor: colors.primarySoft,
        borderRadius: radius.sm,
        gap: 2,
        opacity,
        paddingHorizontal: 16,
        paddingVertical: 14,
        transform: [{ scale }],
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
    </Animated.View>
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
        <DaysCallout days={days} />
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
              ? ` ${tx("Last visit {time}.", { time: formatGymDateTime(data.check_in_summary.last_check_in_at, data.gym.timezone) })}`
              : ""}
          </Text>
        ) : (
          <Text>{tx("Check-in status is unavailable.")}</Text>
        )}
      </Card>
    </>
  );
}
