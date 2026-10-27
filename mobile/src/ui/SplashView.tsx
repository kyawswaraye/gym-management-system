import { useEffect, useRef } from "react";
import {
  Animated,
  Easing,
  Image,
  StatusBar,
  StyleSheet,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { useReducedMotion } from "../a11y/motion";
import { useTx } from "../i18n/useTx";
import { Text } from "./Text";

const splashBackground = "#071422";

export function SplashView({ status }: { status?: string }) {
  const tx = useTx();
  const reduced = useReducedMotion();
  const insets = useSafeAreaInsets();
  const opacity = useRef(new Animated.Value(reduced ? 1 : 0)).current;
  const lift = useRef(new Animated.Value(reduced ? 0 : 10)).current;

  useEffect(() => {
    if (reduced) {
      return;
    }
    Animated.parallel([
      Animated.timing(opacity, {
        duration: 720,
        easing: Easing.out(Easing.cubic),
        toValue: 1,
        useNativeDriver: true,
      }),
      Animated.timing(lift, {
        duration: 720,
        easing: Easing.out(Easing.cubic),
        toValue: 0,
        useNativeDriver: true,
      }),
    ]).start();
  }, [lift, opacity, reduced]);

  return (
    <View style={styles.screen}>
      <StatusBar barStyle="light-content" />
      <Animated.View style={{ opacity, transform: [{ translateY: lift }] }}>
        <Image
          accessibilityIgnoresInvertColors
          accessibilityLabel={tx("GymPilot MM")}
          accessibilityRole="image"
          resizeMode="contain"
          source={require("../assets/gympilot-wordmark.png")}
          style={styles.mark}
        />
      </Animated.View>
      {status ? (
        <Text
          accessibilityLiveRegion="polite"
          style={[styles.status, { bottom: Math.max(insets.bottom, 16) + 18 }]}
        >
          {status}
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    alignItems: "center",
    backgroundColor: splashBackground,
    flex: 1,
    justifyContent: "center",
  },
  mark: {
    height: 320,
    width: 320,
  },
  status: {
    color: "#D5DEE8",
    fontSize: 13,
    letterSpacing: 0.3,
    lineHeight: 18,
    position: "absolute",
  },
});
