import { NavigationContainer } from "@react-navigation/native";
import { StatusBar, StyleSheet } from "react-native";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { MotionProvider } from "./src/a11y/motion";
import { AppProviders } from "./src/app/providers";
import { assertRuntimeEnv, env } from "./src/config/env";
import { RootNavigator } from "./src/navigation/RootNavigator";

assertRuntimeEnv(env, __DEV__);

export default function App() {
  return (
    <GestureHandlerRootView style={styles.root}>
      <SafeAreaProvider>
        <MotionProvider>
          <AppProviders>
            <NavigationContainer>
              <StatusBar barStyle="dark-content" />
              <RootNavigator />
            </NavigationContainer>
          </AppProviders>
        </MotionProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
});
