import { Text as NativeText, type TextProps } from "react-native";

import { useLocale } from "../i18n/useTx";
import { colors } from "./theme";

export function Text({ style, ...props }: TextProps) {
  const locale = useLocale();
  return (
    <NativeText
      allowFontScaling
      maxFontSizeMultiplier={2}
      style={[
        {
          color: colors.text,
          fontSize: 16,
          lineHeight: locale === "mm" ? 28 : 24,
        },
        style,
      ]}
      {...props}
    />
  );
}
