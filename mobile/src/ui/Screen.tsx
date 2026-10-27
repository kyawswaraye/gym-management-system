import { useLocale, useTx } from "../i18n/useTx";
import type { ReactElement, ReactNode } from "react";
import {
  Pressable,
  ScrollView,
  View,
  type RefreshControlProps,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { colors, space, touchTarget } from "./theme";
import { Text } from "./Text";

export function Screen({
  title,
  children,
  refreshControl,
  footer,
  onBack,
  accessory,
  heading,
  scroll = true,
}: {
  title: string;
  children: ReactNode;
  refreshControl?: ReactElement<RefreshControlProps>;
  footer?: ReactNode;
  onBack?: () => void;
  accessory?: ReactNode;
  heading?: ReactNode;
  scroll?: boolean;
}) {
  const tx = useTx();
  const locale = useLocale();
  const header = (
    <>
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
      {heading ?? (
        <View style={{ gap: 10 }}>
          <View
            style={{
              alignItems: "center",
              flexDirection: "row",
              gap: space.sm,
              justifyContent: "space-between",
            }}
          >
            <Text
              accessibilityRole="header"
              style={{
                flex: 1,
                fontSize: 30,
                fontWeight: "700",
                letterSpacing: locale === "mm" ? 0 : -0.6,
                lineHeight: locale === "mm" ? 48 : 36,
              }}
            >
              {tx(title)}
            </Text>
            {accessory}
          </View>
          <View
            style={{
              backgroundColor: colors.accent,
              borderRadius: 2,
              height: 3,
              width: 32,
            }}
          />
        </View>
      )}
    </>
  );
  return (
    <SafeAreaView
      edges={["top", "left", "right"]}
      style={{ backgroundColor: colors.background, flex: 1 }}
    >
      {scroll ? (
        <ScrollView
          contentContainerStyle={{
            gap: space.lg,
            paddingBottom: space.lg,
            paddingHorizontal: 20,
            paddingTop: space.sm,
          }}
          keyboardShouldPersistTaps="handled"
          refreshControl={refreshControl}
        >
          {header}
          {children}
        </ScrollView>
      ) : (
        <View
          style={{
            flex: 1,
            gap: space.md,
            paddingBottom: space.md,
            paddingHorizontal: 20,
            paddingTop: space.sm,
          }}
        >
          {header}
          {children}
        </View>
      )}
      {footer ? (
        <View style={{ padding: space.md, paddingBottom: space.md }}>
          {footer}
        </View>
      ) : null}
    </SafeAreaView>
  );
}
