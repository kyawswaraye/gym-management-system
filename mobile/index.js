import "react-native-gesture-handler";
import { AppRegistry } from "react-native";

import App from "./App";
import { name as appName } from "./app.json";

try {
  const messaging = require("@react-native-firebase/messaging").default;
  // Notification payloads are shown by the OS while the app is backgrounded
  // or closed. This handler only keeps data messages from crashing the process.
  messaging().setBackgroundMessageHandler(async () => undefined);
} catch {
  // Firebase credentials are supplied outside the repository.
}

AppRegistry.registerComponent(appName, () => App);
