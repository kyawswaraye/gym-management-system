import { Pressable, View } from "react-native";

import { useLocale } from "./useTx";
import type { Locale } from "./locale";
import { colors, radius, touchTarget } from "../ui/theme";
import { Text } from "../ui/Text";

export function LanguageSwitch({
  onChange,
}: {
  onChange: (locale: Locale) => void;
}) {
  const locale = useLocale();
  return (
    <View
      accessibilityRole="tablist"
      style={{ flexDirection: "row", alignSelf: "flex-end" }}
    >
      {(["en", "mm"] as const).map((item, index) => {
        const selected = locale === item;
        return (
          <Pressable
            accessibilityRole="button"
            accessibilityState={{ selected }}
            key={item}
            onPress={() => onChange(item)}
            style={{
              minHeight: touchTarget,
              minWidth: touchTarget,
              paddingHorizontal: 12,
              alignItems: "center",
              justifyContent: "center",
              backgroundColor: selected ? colors.primary : colors.surface,
              borderWidth: 1,
              borderColor: colors.primary,
              borderTopLeftRadius: index === 0 ? radius.md : 0,
              borderBottomLeftRadius: index === 0 ? radius.md : 0,
              borderTopRightRadius: index === 1 ? radius.md : 0,
              borderBottomRightRadius: index === 1 ? radius.md : 0,
              marginLeft: index === 0 ? 0 : -1,
            }}
          >
            <Text
              style={{
                color: selected ? colors.onPrimary : colors.primary,
                fontWeight: "700",
              }}
            >
              {item === "en" ? "EN" : "MM"}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}
