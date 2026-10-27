import { useLocale, useTx } from "../i18n/useTx";
import { TextInput, View, type TextInputProps } from "react-native";

import { colors, radius, touchTarget } from "./theme";
import { Text } from "./Text";

type Props = TextInputProps & {
  label: string;
  hint?: string;
  error?: string;
};

export function TextField({ label, hint, error, style, ...props }: Props) {
  const tx = useTx();
  const locale = useLocale();
  const labelText = tx(label);
  const hintText = hint ? tx(hint) : undefined;
  const errorText = error ? tx(error) : undefined;
  const describedBy = errorText ?? hintText;
  return (
    <View style={{ gap: 4 }}>
      <Text style={{ fontWeight: "700" }}>{labelText}</Text>
      <TextInput
        accessibilityLabel={labelText}
        accessibilityHint={describedBy}
        allowFontScaling
        maxFontSizeMultiplier={2}
        placeholderTextColor={colors.muted}
        style={[
          {
            minHeight: touchTarget,
            borderWidth: 1,
            borderColor: error ? colors.danger : colors.border,
            borderRadius: radius.md,
            paddingHorizontal: 12,
            color: colors.text,
            backgroundColor: colors.surface,
            fontSize: 16,
            ...(locale === "mm" ? { lineHeight: 26, paddingVertical: 8 } : {}),
          },
          style,
        ]}
        {...props}
      />
      {error ? (
        <Text accessibilityRole="alert" style={{ color: colors.danger }}>
          {errorText}
        </Text>
      ) : null}
    </View>
  );
}
