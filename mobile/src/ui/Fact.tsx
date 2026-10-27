import { View } from "react-native";

import { colors } from "./theme";
import { Text } from "./Text";

export function Fact({ label, value }: { label: string; value: string }) {
  return (
    <View
      style={{
        alignItems: "flex-start",
        flexDirection: "row",
        gap: 16,
        justifyContent: "space-between",
        paddingVertical: 2,
      }}
    >
      <Text style={{ color: colors.muted, flex: 1 }}>{label}</Text>
      <Text style={{ fontWeight: "700", textAlign: "right" }}>{value}</Text>
    </View>
  );
}
