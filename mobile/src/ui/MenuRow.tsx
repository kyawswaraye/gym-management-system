import { Pressable, View } from "react-native";

import { useTx } from "../i18n/useTx";
import { colors, touchTarget } from "./theme";
import { Text } from "./Text";

export function MenuRow({
  label,
  hint,
  onPress,
  chevron = true,
}: {
  label: string;
  hint?: string;
  onPress: () => void;
  chevron?: boolean;
}) {
  const tx = useTx();
  const labelText = tx(label);
  const hintText = hint ? tx(hint) : undefined;
  return (
    <Pressable
      accessibilityHint={hintText}
      accessibilityLabel={labelText}
      accessibilityRole="button"
      onPress={onPress}
      style={({ pressed }) => ({
        alignItems: "center",
        flexDirection: "row",
        gap: 12,
        justifyContent: "space-between",
        minHeight: touchTarget,
        opacity: pressed ? 0.72 : 1,
        paddingVertical: 12,
      })}
    >
      <View style={{ flex: 1, gap: 2 }}>
        <Text style={{ fontWeight: "600" }}>{labelText}</Text>
        {hintText ? (
          <Text style={{ color: colors.muted, fontSize: 14 }}>{hintText}</Text>
        ) : null}
      </View>
      {chevron ? (
        <Text style={{ color: colors.muted, fontSize: 22 }}>›</Text>
      ) : null}
    </Pressable>
  );
}
