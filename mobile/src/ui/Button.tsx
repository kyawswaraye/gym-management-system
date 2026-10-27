import {
  Pressable,
  type PressableProps,
  type StyleProp,
  type ViewStyle,
} from "react-native";

import { useTx } from "../i18n/useTx";
import { colors, radius, touchTarget } from "./theme";
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
  const tx = useTx();
  const labelText = tx(label);
  const hintText = hint ? tx(hint) : undefined;
  const background =
    variant === "primary"
      ? colors.primary
      : variant === "danger"
        ? colors.danger
        : colors.surface;
  const color = variant === "secondary" ? colors.text : colors.onPrimary;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={labelText}
      accessibilityHint={hintText}
      accessibilityState={{ disabled: Boolean(disabled) }}
      disabled={disabled}
      style={({ pressed }) => [
        {
          minHeight: touchTarget,
          minWidth: touchTarget,
          paddingHorizontal: 16,
          borderRadius: radius.md,
          alignItems: "center",
          justifyContent: "center",
          backgroundColor: background,
          borderWidth: variant === "secondary" ? 1 : 0,
          borderColor: colors.border,
          opacity: disabled ? 0.5 : pressed ? 0.86 : 1,
        },
        style,
      ]}
      {...props}
    >
      <Text style={{ color, fontWeight: "700" }}>{labelText}</Text>
    </Pressable>
  );
}
