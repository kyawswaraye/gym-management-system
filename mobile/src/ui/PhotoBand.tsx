import type { ReactNode } from "react";
import { useEffect, useRef } from "react";
import {
  Animated,
  Image,
  StyleSheet,
  View,
  type ImageSourcePropType,
  type StyleProp,
  type ViewStyle,
} from "react-native";

import { useReducedMotion } from "../a11y/motion";
import { colors, radius } from "./theme";

type Props = {
  source: ImageSourcePropType;
  height: number;
  children?: ReactNode;
  /** Full wash, bottom-only fade, or none. */
  scrim?: "full" | "bottom" | "none";
  /** Soften the photo so brand/text sit cleaner on top. */
  blurRadius?: number;
  rounded?: boolean;
  style?: StyleProp<ViewStyle>;
  accessibilityLabel?: string;
  animate?: boolean;
};

export function PhotoBand({
  source,
  height,
  children,
  scrim = "bottom",
  blurRadius = 5,
  rounded = false,
  style,
  accessibilityLabel,
  animate = false,
}: Props) {
  const reduced = useReducedMotion();
  const opacity = useRef(
    new Animated.Value(reduced || !animate ? 1 : 0),
  ).current;
  const translateY = useRef(
    new Animated.Value(reduced || !animate ? 0 : 12),
  ).current;

  useEffect(() => {
    if (!animate || reduced) {
      opacity.setValue(1);
      translateY.setValue(0);
      return;
    }
    Animated.parallel([
      Animated.timing(opacity, {
        toValue: 1,
        duration: 480,
        useNativeDriver: true,
      }),
      Animated.timing(translateY, {
        toValue: 0,
        duration: 480,
        useNativeDriver: true,
      }),
    ]).start();
  }, [animate, opacity, reduced, translateY]);

  return (
    <Animated.View
      accessibilityLabel={accessibilityLabel}
      accessibilityRole="image"
      style={[
        {
          borderRadius: rounded ? radius.md : 0,
          height,
          overflow: "hidden",
          opacity,
          transform: [{ translateY }],
        },
        style,
      ]}
    >
      <Image
        accessibilityIgnoresInvertColors
        blurRadius={blurRadius}
        resizeMode="cover"
        source={source}
        style={[
          StyleSheet.absoluteFill,
          // Scale past the frame so soft blur edges do not show.
          { transform: [{ scale: 1.04 }] },
        ]}
      />
      <View
        pointerEvents="none"
        style={[StyleSheet.absoluteFill, { backgroundColor: colors.photoWash }]}
      />
      {scrim === "full" ? (
        <View
          pointerEvents="none"
          style={[StyleSheet.absoluteFill, { backgroundColor: colors.scrim }]}
        />
      ) : null}
      {scrim === "bottom" ? (
        <View
          pointerEvents="none"
          style={{
            backgroundColor: colors.scrim,
            bottom: 0,
            height: "48%",
            left: 0,
            position: "absolute",
            right: 0,
          }}
        />
      ) : null}
      {children ? (
        <View
          style={[
            StyleSheet.absoluteFill,
            { justifyContent: "flex-end", padding: 20 },
          ]}
        >
          {children}
        </View>
      ) : null}
    </Animated.View>
  );
}
