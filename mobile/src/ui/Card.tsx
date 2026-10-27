import type { ReactNode } from "react";
import { View } from "react-native";

import { colors, elevation, radius, space } from "./theme";

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
        gap: padded ? space.md : 0,
        padding: padded ? 18 : 0,
        ...elevation,
      }}
    >
      {children}
    </View>
  );
}
