import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { RefreshControl, View } from "react-native";

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
import { Screen } from "../ui/Screen";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  OfflineBanner,
} from "../ui/StateView";
import { Text } from "../ui/Text";

export function ScheduleScreen() {
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
      setMessage("Choose a class package with remaining sessions.");
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
      setMessage("Class booked.");
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
      title="Schedule"
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
      <Text>
        Showing {range.date_from} through {range.date_to}, 42 gym-local days.
      </Text>
      {schedule.isLoading ? <LoadingState label="Loading schedule" /> : null}
      {schedule.isError ? (
        <ErrorState
          error={schedule.error}
          offline={!online}
          onRetry={() => schedule.refetch()}
        />
      ) : null}
      {message ? <Text accessibilityRole="alert">{message}</Text> : null}
      <Text accessibilityRole="header" style={{ fontWeight: "700" }}>
        Your bookings
      </Text>
      {schedule.data && schedule.data.bookings.length === 0 ? (
        <EmptyState message="No bookings in this window." />
      ) : null}
      {schedule.data?.bookings.map((booking) => (
        <BookingRow key={booking.id} booking={booking} />
      ))}
      <Text accessibilityRole="header" style={{ fontWeight: "700" }}>
        Classes
      </Text>
      {classPackages.length === 0 ? (
        <Text>
          No active class package with remaining sessions is available for
          booking.
        </Text>
      ) : (
        classPackages.map((item) => (
          <Button
            key={item.id}
            label={`${item.package_name}, ${item.remaining_sessions} remaining`}
            variant={selectedPackage === item.id ? "primary" : "secondary"}
            hint="Selects this package for the next class booking"
            onPress={() => setSelectedPackage(item.id)}
          />
        ))
      )}
      {schedule.data && schedule.data.occurrences.length === 0 ? (
        <EmptyState message="No classes are scheduled in this window." />
      ) : null}
      {schedule.data?.occurrences.map((occurrence) => (
        <View key={occurrence.id} style={{ gap: 8 }}>
          <Text>
            {occurrence.class_name} · {formatGymDateTime(occurrence.starts_at)}
          </Text>
          <Text>
            {occurrence.trainer_display_name}
            {occurrence.location ? ` · ${occurrence.location}` : ""} ·{" "}
            {occurrence.booked_count}/{occurrence.capacity}
          </Text>
          {occurrence.member_booking_id ? (
            <Text>You are booked.</Text>
          ) : (
            <Button
              label="Book class"
              hint={`Books ${occurrence.class_name}`}
              disabled={
                !selectedPackage || bookingId === occurrence.id || !online
              }
              onPress={() => book(occurrence.id)}
            />
          )}
        </View>
      ))}
    </Screen>
  );
}

function BookingRow({
  booking,
}: {
  booking: Schemas["MemberScheduleBooking"];
}) {
  return (
    <View>
      <Text>
        {booking.kind === "CLASS"
          ? (booking.class_name ?? "Class")
          : "Personal training"}{" "}
        · {booking.status}
      </Text>
      <Text>
        {formatGymDateTime(booking.starts_at)} to{" "}
        {formatGymDateTime(booking.ends_at)}
      </Text>
      <Text>{booking.trainer_display_name}</Text>
    </View>
  );
}
