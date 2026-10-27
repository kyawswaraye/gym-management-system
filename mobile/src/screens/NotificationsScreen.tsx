import { useTx } from "../i18n/useTx";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { useMemberApi } from "../app/providers";
import { formatGymDateTime } from "../dates/gymLocal";
import { queryKeys } from "../query/keys";
import { Button } from "../ui/Button";
import { PagedScreen } from "../ui/PagedScreen";
import { Text } from "../ui/Text";
import { colors } from "../ui/theme";

export function NotificationsScreen() {
  const tx = useTx();
  const api = useMemberApi();
  const queryClient = useQueryClient();
  const [pendingId, setPendingId] = useState<string | null>(null);
  return (
    <PagedScreen
      title={tx("Inbox")}
      queryKey={queryKeys.notifications}
      load={(after) => api.notifications(after)}
      empty="No messages yet. Push permission is not required to read messages here."
      renderItem={(item) => (
        <>
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
              : ""} · {formatGymDateTime(item.created_at)}
          </Text>
          {item.read_at ? (
            <Text>
              {tx("Read")} {formatGymDateTime(item.read_at)}
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
        </>
      )}
    />
  );
}
