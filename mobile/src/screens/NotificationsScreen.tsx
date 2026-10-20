import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { useMemberApi } from "../app/providers";
import { formatGymDateTime } from "../dates/gymLocal";
import { queryKeys } from "../query/keys";
import { Button } from "../ui/Button";
import { PagedScreen } from "../ui/PagedScreen";
import { Text } from "../ui/Text";

export function NotificationsScreen() {
  const api = useMemberApi();
  const queryClient = useQueryClient();
  const [pendingId, setPendingId] = useState<string | null>(null);
  return (
    <PagedScreen
      title="Inbox"
      queryKey={queryKeys.notifications}
      load={(after) => api.notifications(after)}
      empty="No messages yet. Push permission is not required to read messages here."
      renderItem={(item) => (
        <>
          <Text style={{ fontWeight: item.read_at ? "400" : "700" }}>
            {item.title}
          </Text>
          <Text>{item.body}</Text>
          <Text>
            {item.type}
            {item.announcement_type
              ? ` · ${item.announcement_type}`
              : ""} · {formatGymDateTime(item.created_at)}
          </Text>
          {item.read_at ? (
            <Text>Read {formatGymDateTime(item.read_at)}</Text>
          ) : (
            <Button
              label="Mark read"
              hint={`Marks ${item.title} as read`}
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
