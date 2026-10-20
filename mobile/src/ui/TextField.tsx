import { TextInput, View, type TextInputProps } from "react-native";

import { colors, touchTarget } from "./theme";
import { Text } from "./Text";

type Props = TextInputProps & {
  label: string;
  hint?: string;
  error?: string;
};

export function TextField({ label, hint, error, style, ...props }: Props) {
  const describedBy = error ?? hint;
  return (
    <View style={{ gap: 4 }}>
      <Text style={{ fontWeight: "700" }}>{label}</Text>
      <TextInput
        accessibilityLabel={label}
        accessibilityHint={describedBy}
        allowFontScaling
        maxFontSizeMultiplier={2}
        placeholderTextColor={colors.muted}
        style={[
          {
            minHeight: touchTarget,
            borderWidth: 1,
            borderColor: error ? colors.danger : colors.border,
            borderRadius: 8,
            paddingHorizontal: 12,
            color: colors.text,
            backgroundColor: colors.surface,
            fontSize: 16,
          },
          style,
        ]}
        {...props}
      />
      {error ? (
        <Text accessibilityRole="alert" style={{ color: colors.danger }}>
          {error}
        </Text>
      ) : null}
    </View>
  );
}
