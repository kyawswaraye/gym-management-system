import type { ReactNode } from "react";
import { View } from "react-native";

import { colors, radius, space } from "./theme";

export function Card({
  children,
  padded = true,
}: {
  children: ReactNode;
  padded?: boolean;
}) {
  return (
    <View
      style={{
        backgroundColor: colors.surface,
        borderColor: colors.border,
        borderRadius: radius.md,
        borderWidth: 1,
        gap: padded ? space.sm : 0,
        overflow: "hidden",
        padding: padded ? space.md : 0,
      }}
    >
      {children}
    </View>
  );
}
