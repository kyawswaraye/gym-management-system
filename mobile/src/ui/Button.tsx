import {
  Pressable,
  type PressableProps,
  type StyleProp,
  type ViewStyle,
} from "react-native";

import { colors, touchTarget } from "./theme";
import { Text } from "./Text";

type Props = Omit<PressableProps, "style"> & {
  label: string;
  hint?: string;
  variant?: "primary" | "secondary" | "danger";
  style?: StyleProp<ViewStyle>;
};

export function Button({
  label,
  hint,
  variant = "primary",
  disabled,
  style,
  ...props
}: Props) {
  const background =
    variant === "primary"
      ? colors.primary
      : variant === "danger"
        ? colors.danger
        : colors.surface;
  const color = variant === "secondary" ? colors.primary : colors.onPrimary;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityHint={hint}
      accessibilityState={{ disabled: Boolean(disabled) }}
      disabled={disabled}
      style={[
        {
          minHeight: touchTarget,
          minWidth: touchTarget,
          paddingHorizontal: 16,
          borderRadius: 8,
          alignItems: "center",
          justifyContent: "center",
          backgroundColor: background,
          borderWidth: variant === "secondary" ? 1 : 0,
          borderColor: colors.primary,
          opacity: disabled ? 0.5 : 1,
        },
        style,
      ]}
      {...props}
    >
      <Text style={{ color, fontWeight: "700" }}>{label}</Text>
    </Pressable>
  );
}
