import type { ReactNode } from "react";
import { RefreshControl, View } from "react-native";

import type { Page } from "../api/memberApi";
import { useOnline } from "../app/providers";
import { useCursorList } from "../pagination/useCursorList";
import { Button } from "./Button";
import { Screen } from "./Screen";
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
}: {
  title: string;
  queryKey: readonly unknown[];
  load: (after?: string) => Promise<Page<T>>;
  empty: string;
  renderItem: (item: T) => ReactNode;
}) {
  const online = useOnline();
  const query = useCursorList(queryKey, load);
  return (
    <Screen
      title={title}
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
        <LoadingState label={`Loading ${title.toLowerCase()}`} />
      ) : null}
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
      {query.items.map((item) => (
        <View key={item.id}>{renderItem(item)}</View>
      ))}
      {query.hasNextPage ? (
        <Button
          label={query.isFetchingNextPage ? "Loading more" : "Load more"}
          hint="Loads the next page"
          disabled={query.isFetchingNextPage}
          onPress={() => query.fetchNextPage()}
        />
      ) : null}
    </Screen>
  );
}
