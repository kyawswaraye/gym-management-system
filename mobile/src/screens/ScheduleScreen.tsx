import { useTx } from "../i18n/useTx";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { RefreshControl } from "react-native";

import { ApiError } from "../api/errors";
import type { Schemas } from "../api/memberApi";
import { useMemberApi, useOnline } from "../app/providers";
import { createIdempotencyKey } from "../booking/idempotency";
import {
  calendarDateFromLocal,
  formatGymDateTime,
  scheduleWindow,
} from "../dates/gymLocal";
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
import { colors } from "../ui/theme";

export function ScheduleScreen() {
  const tx = useTx();
  const api = useMemberApi();
  const online = useOnline();
  const queryClient = useQueryClient();
  const range = scheduleWindow(calendarDateFromLocal(new Date()));
  const schedule = useQuery({
    queryKey: queryKeys.schedule(range.date_from, range.date_to),
    queryFn: () => api.schedule(range.date_from, range.date_to),
  });
  const packages = useQuery({
    queryKey: queryKeys.packages,
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
      <Text style={{ color: colors.muted }}>
        {tx("Showing")} {range.date_from} {tx("through")} {range.date_to}
        {tx(", 42 gym-local days.")}
      </Text>
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
      {schedule.data?.bookings.map((booking) => (
        <BookingRow key={booking.id} booking={booking} />
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
        classPackages.map((item) => (
          <Button
            key={item.id}
            label={tx("{name}, {count} remaining", {
              name: item.package_name,
              count: item.remaining_sessions,
            })}
            variant={selectedPackage === item.id ? "primary" : "secondary"}
            hint={tx("Selects this package for the next class booking")}
            onPress={() => setSelectedPackage(item.id)}
          />
        ))
      )}
      {schedule.data && schedule.data.occurrences.length === 0 ? (
        <EmptyState message={tx("No classes are scheduled in this window.")} />
      ) : null}
      {schedule.data?.occurrences.map((occurrence) => (
        <Card key={occurrence.id}>
          <Text style={{ fontWeight: "700" }}>
            {occurrence.class_name} · {formatGymDateTime(occurrence.starts_at)}
          </Text>
          <Text style={{ color: colors.muted }}>
            {occurrence.trainer_display_name}
            {occurrence.location ? ` · ${occurrence.location}` : ""} ·{" "}
            {occurrence.booked_count}/{occurrence.capacity}
          </Text>
          {occurrence.member_booking_id ? (
            <Text>{tx("You are booked.")}</Text>
          ) : (
            <Button
              label={tx("Book class")}
              hint={tx("Books {name}", { name: occurrence.class_name })}
              disabled={
                !selectedPackage || bookingId === occurrence.id || !online
              }
              onPress={() => book(occurrence.id)}
            />
          )}
        </Card>
      ))}
    </Screen>
  );
}

function BookingRow({
  booking,
}: {
  booking: Schemas["MemberScheduleBooking"];
}) {
  const tx = useTx();
  return (
    <Card>
      <Text style={{ fontWeight: "700" }}>
        {booking.kind === "CLASS"
          ? (booking.class_name ?? tx("Class"))
          : tx("Personal training")}{" "}
        · {tx(booking.status)}
      </Text>
      <Text style={{ color: colors.muted }}>
        {formatGymDateTime(booking.starts_at)} {tx("to")}{" "}
        {formatGymDateTime(booking.ends_at)}
      </Text>
      <Text style={{ color: colors.muted }}>
        {booking.trainer_display_name}
      </Text>
    </Card>
  );
}
