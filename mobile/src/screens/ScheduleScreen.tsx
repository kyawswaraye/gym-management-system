import { useTx } from "../i18n/useTx";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Pressable, RefreshControl, ScrollView, View } from "react-native";

import { ApiError } from "../api/errors";
import type { Schemas } from "../api/memberApi";
import { useMemberApi, useOnline } from "../app/providers";
import { createIdempotencyKey } from "../booking/idempotency";
import {
  calendarDateFromLocal,
  formatGymDate,
  formatGymTime,
  gymLocalCalendarDate,
  scheduleWindow,
} from "../dates/gymLocal";
import { useGymTimezone } from "../dates/useGymTimezone";
import { queryKeys } from "../query/keys";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Screen } from "../ui/Screen";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  OfflineBanner,
} from "../ui/StateView";
import { Text } from "../ui/Text";
import { colors, radius, space } from "../ui/theme";

export function ScheduleScreen() {
  const tx = useTx();
  const api = useMemberApi();
  const online = useOnline();
  const queryClient = useQueryClient();
  const timeZone = useGymTimezone();
  const range = scheduleWindow(calendarDateFromLocal(new Date()));
  const schedule = useQuery({
    queryKey: queryKeys.schedule(range.date_from, range.date_to),
    queryFn: () => api.schedule(range.date_from, range.date_to),
  });
  const packages = useQuery({
    queryKey: queryKeys.bookingPackages,
    queryFn: () => api.servicePackages(),
  });
  const classPackages = (packages.data?.items ?? []).filter(
    (item) =>
      item.package_type === "CLASS" &&
      item.status === "ACTIVE" &&
      item.remaining_sessions >= 1,
  );
  const [selectedPackage, setSelectedPackage] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [bookingId, setBookingId] = useState<string | null>(null);
  const keys = useRef(new Map<string, string>());

  async function book(occurrenceId: string) {
    if (!selectedPackage) {
      setMessage(tx("Choose a class package with remaining sessions."));
      return;
    }
    const attempt = `${occurrenceId}:${selectedPackage}`;
    const idempotencyKey = keys.current.get(attempt) ?? createIdempotencyKey();
    keys.current.set(attempt, idempotencyKey);
    setBookingId(occurrenceId);
    setMessage(null);
    try {
      await api.bookClass(
        {
          occurrence_id: occurrenceId,
          member_service_package_id: selectedPackage,
        },
        idempotencyKey,
      );
      keys.current.delete(attempt);
      setMessage(tx("Class booked."));
      await queryClient.invalidateQueries({
        queryKey: queryKeys.schedule(range.date_from, range.date_to),
      });
    } catch (error) {
      if (
        error instanceof ApiError &&
        error.code === "IDEMPOTENCY_KEY_REUSED"
      ) {
        keys.current.delete(attempt);
      }
      setMessage(
        error instanceof Error
          ? error.message
          : "The class could not be booked.",
      );
    } finally {
      setBookingId(null);
    }
  }

  return (
    <Screen
      title={tx("Schedule")}
      refreshControl={
        <RefreshControl
          refreshing={schedule.isRefetching}
          onRefresh={() => {
            void schedule.refetch();
            void packages.refetch();
          }}
        />
      }
    >
      <OfflineBanner visible={!online} />
      <Card>
        <Text style={{ color: colors.muted, fontSize: 13 }}>
          {tx("Showing")}
        </Text>
        <Text style={{ fontSize: 18, fontWeight: "700" }}>
          {formatGymDate(range.date_from)} {tx("to")}{" "}
          {formatGymDate(range.date_to)}
        </Text>
      </Card>
      {schedule.isLoading ? (
        <LoadingState label={tx("Loading schedule")} />
      ) : null}
      {schedule.isError ? (
        <ErrorState
          error={schedule.error}
          offline={!online}
          onRetry={() => schedule.refetch()}
        />
      ) : null}
      {message ? <Text accessibilityRole="alert">{message}</Text> : null}
      <Text
        accessibilityRole="header"
        style={{ fontSize: 20, fontWeight: "700" }}
      >
        {tx("Your bookings")}
      </Text>
      {schedule.data && schedule.data.bookings.length === 0 ? (
        <EmptyState message={tx("No bookings in this window.")} />
      ) : null}
      {groupByStart(schedule.data?.bookings ?? [], timeZone).map((group) => (
        <View key={`booking-${group.date}`} style={{ gap: space.sm }}>
          <Text style={{ color: colors.primary, fontWeight: "700" }}>
            {formatGymDate(group.date)}
          </Text>
          {group.items.map((booking) => (
            <BookingRow
              key={booking.id}
              booking={booking}
              timeZone={timeZone}
            />
          ))}
        </View>
      ))}
      <Text
        accessibilityRole="header"
        style={{ fontSize: 20, fontWeight: "700" }}
      >
        {tx("Classes")}
      </Text>
      {classPackages.length === 0 ? (
        <Text style={{ color: colors.muted }}>
          {tx(
            "No active class package with remaining sessions is available for booking.",
          )}
        </Text>
      ) : (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={{ gap: space.sm }}
        >
          {classPackages.map((item) => {
            const selected = selectedPackage === item.id;
            return (
              <Pressable
                key={item.id}
                accessibilityHint={tx(
                  "Selects this package for the next class booking",
                )}
                accessibilityRole="button"
                accessibilityState={{ selected }}
                onPress={() => setSelectedPackage(item.id)}
                style={{
                  backgroundColor: selected ? colors.primary : colors.surface,
                  borderColor: selected ? colors.primary : colors.border,
                  borderRadius: radius.md,
                  borderWidth: 1,
                  justifyContent: "center",
                  minHeight: 44,
                  paddingHorizontal: 14,
                }}
              >
                <Text
                  style={{
                    color: selected ? colors.onPrimary : colors.text,
                    fontWeight: "700",
                  }}
                >
                  {item.package_name}
                </Text>
                <Text
                  style={{
                    color: selected ? colors.onPrimary : colors.muted,
                    fontSize: 13,
                  }}
                >
                  {tx("{count} remaining", { count: item.remaining_sessions })}
                </Text>
              </Pressable>
            );
          })}
        </ScrollView>
      )}
      {classPackages.length > 0 && !selectedPackage ? (
        <Text style={{ color: colors.muted }}>
          {tx("Choose a class package with remaining sessions.")}
        </Text>
      ) : null}
      {schedule.data && schedule.data.occurrences.length === 0 ? (
        <EmptyState message={tx("No classes are scheduled in this window.")} />
      ) : null}
      {groupByStart(schedule.data?.occurrences ?? [], timeZone).map((group) => (
        <View key={`class-${group.date}`} style={{ gap: space.sm }}>
          <Text style={{ color: colors.primary, fontWeight: "700" }}>
            {formatGymDate(group.date)}
          </Text>
          {group.items.map((occurrence) => {
            const full = occurrence.booked_count >= occurrence.capacity;
            const width =
              occurrence.capacity > 0
                ? Math.min(
                    100,
                    (occurrence.booked_count / occurrence.capacity) * 100,
                  )
                : 0;
            return (
              <Card key={occurrence.id}>
                <View style={{ flexDirection: "row", gap: 12 }}>
                  <View style={{ width: 58 }}>
                    <Text style={{ color: colors.primary, fontWeight: "700" }}>
                      {formatGymTime(occurrence.starts_at, timeZone)}
                    </Text>
                    <Text style={{ color: colors.muted, fontSize: 13 }}>
                      {formatGymTime(occurrence.ends_at, timeZone)}
                    </Text>
                  </View>
                  <View style={{ flex: 1, gap: 6 }}>
                    <Text style={{ fontSize: 17, fontWeight: "700" }}>
                      {occurrence.class_name}
                    </Text>
                    <Text style={{ color: colors.muted }}>
                      {occurrence.trainer_display_name}
                      {occurrence.location ? ` · ${occurrence.location}` : ""}
                    </Text>
                    <View
                      style={{
                        backgroundColor: colors.border,
                        borderRadius: 4,
                        height: 6,
                        overflow: "hidden",
                      }}
                    >
                      <View
                        style={{
                          backgroundColor: full
                            ? colors.danger
                            : colors.primary,
                          height: 6,
                          width: `${width}%`,
                        }}
                      />
                    </View>
                    <Text style={{ color: colors.muted, fontSize: 13 }}>
                      {tx("{booked} of {capacity}", {
                        booked: occurrence.booked_count,
                        capacity: occurrence.capacity,
                      })}
                      {full ? ` · ${tx("Full")}` : ""}
                    </Text>
                    {occurrence.member_booking_id ? (
                      <Text
                        style={{ color: colors.primary, fontWeight: "700" }}
                      >
                        {tx("You are booked.")}
                      </Text>
                    ) : (
                      <Button
                        label={tx("Book class")}
                        hint={tx("Books {name}", {
                          name: occurrence.class_name,
                        })}
                        disabled={
                          !selectedPackage ||
                          bookingId === occurrence.id ||
                          !online ||
                          full
                        }
                        onPress={() => book(occurrence.id)}
                      />
                    )}
                  </View>
                </View>
              </Card>
            );
          })}
        </View>
      ))}
    </Screen>
  );
}

function BookingRow({
  booking,
  timeZone,
}: {
  booking: Schemas["MemberScheduleBooking"];
  timeZone: string;
}) {
  const tx = useTx();
  const title =
    booking.kind === "CLASS"
      ? (booking.class_name ?? tx("Class"))
      : tx("Personal training");
  return (
    <Card>
      <View style={{ flexDirection: "row", gap: 12 }}>
        <View style={{ width: 58 }}>
          <Text style={{ color: colors.primary, fontWeight: "700" }}>
            {formatGymTime(booking.starts_at, timeZone)}
          </Text>
          <Text style={{ color: colors.muted, fontSize: 13 }}>
            {formatGymTime(booking.ends_at, timeZone)}
          </Text>
        </View>
        <View style={{ flex: 1, gap: 4 }}>
          <Text style={{ fontSize: 17, fontWeight: "700" }}>{title}</Text>
          <Text style={{ color: colors.muted }}>
            {booking.trainer_display_name}
          </Text>
          <Text
            style={{
              alignSelf: "flex-start",
              backgroundColor: colors.primarySoft,
              borderRadius: 999,
              color: colors.primary,
              fontSize: 13,
              fontWeight: "700",
              overflow: "hidden",
              paddingHorizontal: 10,
              paddingVertical: 4,
            }}
          >
            {tx(booking.status)}
          </Text>
        </View>
      </View>
    </Card>
  );
}

function groupByStart<T extends { starts_at: string }>(
  items: readonly T[],
  timeZone: string,
) {
  const order: string[] = [];
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const date = gymLocalCalendarDate(item.starts_at, timeZone);
    const list = groups.get(date);
    if (list) {
      list.push(item);
    } else {
      groups.set(date, [item]);
      order.push(date);
    }
  }
  return order.map((date) => ({ date, items: groups.get(date) ?? [] }));
}
