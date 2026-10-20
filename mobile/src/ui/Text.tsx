import { Text as NativeText, type TextProps } from "react-native";

import { colors } from "./theme";

export function Text({ style, ...props }: TextProps) {
  return (
    <NativeText
      allowFontScaling
      maxFontSizeMultiplier={2}
      style={[{ color: colors.text, fontSize: 16, lineHeight: 24 }, style]}
      {...props}
    />
  );
}
