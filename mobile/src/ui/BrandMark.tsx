import { Image } from "react-native";

export function BrandMark({ size = 96 }: { size?: number }) {
  return (
    <Image
      accessibilityIgnoresInvertColors
      source={require("../assets/gympilot-mark.png")}
      style={{ width: size, height: size }}
    />
  );
}
