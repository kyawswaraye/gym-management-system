import { useTx } from "../i18n/useTx";
import { useQueryClient } from "@tanstack/react-query";
import { useRoute, type RouteProp } from "@react-navigation/native";
import { useState } from "react";
import { View } from "react-native";

import { useMemberApi } from "../app/providers";
import type { InboxStackParamList } from "../navigation/types";
import { formatGymDateTime } from "../dates/gymLocal";
import { useGymTimezone } from "../dates/useGymTimezone";
import { queryKeys } from "../query/keys";
import { Button } from "../ui/Button";
import { PagedScreen } from "../ui/PagedScreen";
import { Text } from "../ui/Text";
import { colors } from "../ui/theme";

export function NotificationsScreen() {
  const tx = useTx();
  const api = useMemberApi();
  const queryClient = useQueryClient();
  const timeZone = useGymTimezone();
  const [pendingId, setPendingId] = useState<string | null>(null);
  const route = useRoute<RouteProp<InboxStackParamList, "Inbox">>();
  const focusId = route.params?.notificationId;
  return (
    <PagedScreen
      title={tx("Inbox")}
      queryKey={queryKeys.notifications}
      load={(after) => api.notifications(after)}
      empty="No messages yet. Push permission is not required to read messages here."
      lead={
        <Text style={{ color: colors.muted }}>
          {tx(
            "Messages from your gym: membership expiry reminders and announcements sent by the gym owner or manager. You do not send messages from here.",
          )}
        </Text>
      }
      renderItem={(item) => (
        <View
          accessibilityLabel={
            item.id === focusId ? "Opened notification" : undefined
          }
        >
          <Text
            style={{ fontSize: 17, fontWeight: item.read_at ? "400" : "700" }}
          >
            {item.title}
          </Text>
          <Text>{item.body}</Text>
          <Text style={{ color: colors.muted }}>
            {tx(item.type)}
            {item.announcement_type
              ? ` · ${tx(item.announcement_type)}`
              : ""} · {formatGymDateTime(item.created_at, timeZone)}
          </Text>
          {item.read_at ? (
            <Text>
              {tx("Read")} {formatGymDateTime(item.read_at, timeZone)}
            </Text>
          ) : (
            <Button
              label={tx("Mark read")}
              hint={tx("Marks {title} as read", { title: item.title })}
              variant="secondary"
              disabled={pendingId === item.id}
              onPress={async () => {
                setPendingId(item.id);
                try {
                  await api.markNotificationRead(item.id);
                  await queryClient.invalidateQueries({
                    queryKey: queryKeys.notifications,
                  });
                } finally {
                  setPendingId(null);
                }
              }}
            />
          )}
        </View>
      )}
    />
  );
}
