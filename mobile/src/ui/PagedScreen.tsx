import { useTx } from "../i18n/useTx";
import type { ReactNode } from "react";
import { RefreshControl } from "react-native";

import type { Page } from "../api/memberApi";
import { useOnline } from "../app/providers";
import { useCursorList } from "../pagination/useCursorList";
import { Button } from "./Button";
import { Card } from "./Card";
import { Screen } from "./Screen";
import { Text } from "./Text";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  OfflineBanner,
} from "./StateView";

export function PagedScreen<T extends { id: string }>({
  title,
  queryKey,
  load,
  empty,
  renderItem,
  onBack,
  lead,
}: {
  title: string;
  queryKey: readonly unknown[];
  load: (after?: string) => Promise<Page<T>>;
  empty: string;
  renderItem: (item: T) => ReactNode;
  onBack?: () => void;
  lead?: ReactNode;
}) {
  const tx = useTx();
  const online = useOnline();
  const query = useCursorList(queryKey, load);
  return (
    <Screen
      title={title}
      onBack={onBack}
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
      {lead ?? null}
      {query.isLoading ? <LoadingState label={tx("Loading…")} /> : null}
      {query.isError ? (
        <ErrorState
          error={query.error}
          offline={!online}
          onRetry={() => query.refetch()}
        />
      ) : null}
      {query.isSuccess && query.items.length === 0 ? (
        <EmptyState message={empty} />
      ) : null}
      {query.items.map((item, index) =>
        item && typeof item.id === "string" ? (
          <Card key={item.id}>{renderItem(item)}</Card>
        ) : (
          <Card key={`row-${index}`}>
            <Text>{tx("This record could not be shown.")}</Text>
          </Card>
        ),
      )}
      {query.hasNextPage ? (
        <Button
          label={
            query.isFetchingNextPage ? tx("Loading more") : tx("Load more")
          }
          hint={tx("Loads the next page")}
          disabled={query.isFetchingNextPage}
          onPress={() => query.fetchNextPage()}
        />
      ) : null}
    </Screen>
  );
}
