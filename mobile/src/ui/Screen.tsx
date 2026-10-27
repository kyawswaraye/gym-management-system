import { useLocale, useTx } from "../i18n/useTx";
import type { ReactElement, ReactNode } from "react";
import {
  Pressable,
  ScrollView,
  View,
  type RefreshControlProps,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { colors, space, touchTarget } from "./theme";
import { Text } from "./Text";

export function Screen({
  title,
  children,
  refreshControl,
  footer,
  onBack,
}: {
  title: string;
  children: ReactNode;
  refreshControl?: ReactElement<RefreshControlProps>;
  footer?: ReactNode;
  onBack?: () => void;
}) {
  const tx = useTx();
  const locale = useLocale();
  const insets = useSafeAreaInsets();
  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <ScrollView
        contentContainerStyle={{
          gap: space.lg,
          paddingBottom: insets.bottom + space.lg,
          paddingHorizontal: 20,
          paddingTop: insets.top + space.sm,
        }}
        refreshControl={refreshControl}
      >
        {onBack ? (
          <Pressable
            accessibilityLabel={tx("Back")}
            accessibilityRole="button"
            onPress={onBack}
            style={{
              alignSelf: "flex-start",
              justifyContent: "center",
              minHeight: touchTarget,
            }}
          >
            <Text style={{ color: colors.primary, fontWeight: "700" }}>
              {tx("Back")}
            </Text>
          </Pressable>
        ) : null}
        <Text
          accessibilityRole="header"
          style={{
            fontSize: 32,
            fontWeight: "700",
            letterSpacing: locale === "mm" ? 0 : -0.4,
            lineHeight: locale === "mm" ? 50 : 38,
          }}
        >
          {tx(title)}
        </Text>
        {children}
      </ScrollView>
      {footer ? (
        <View
          style={{
            padding: space.md,
            paddingBottom: insets.bottom + space.md,
          }}
        >
          {footer}
        </View>
      ) : null}
    </View>
  );
}
