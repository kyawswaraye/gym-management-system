import type { ReactElement, ReactNode } from "react";
import { ScrollView, View, type RefreshControlProps } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { colors, space } from "./theme";
import { Text } from "./Text";

export function Screen({
  title,
  children,
  refreshControl,
  footer,
}: {
  title: string;
  children: ReactNode;
  refreshControl?: ReactElement<RefreshControlProps>;
  footer?: ReactNode;
}) {
  const insets = useSafeAreaInsets();
  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <ScrollView
        contentContainerStyle={{
          paddingTop: insets.top + space.md,
          paddingBottom: insets.bottom + space.lg,
          paddingHorizontal: space.md,
          gap: space.md,
        }}
        refreshControl={refreshControl}
      >
        <Text
          accessibilityRole="header"
          style={{ fontSize: 28, lineHeight: 34, fontWeight: "700" }}
        >
          {title}
        </Text>
        {children}
      </ScrollView>
      {footer ? (
        <View
          style={{ padding: space.md, paddingBottom: insets.bottom + space.md }}
        >
          {footer}
        </View>
      ) : null}
    </View>
  );
}
