import "react-native-gesture-handler";
import { AppRegistry } from "react-native";

import App from "./App";
import { name as appName } from "./app.json";

try {
  const messaging = require("@react-native-firebase/messaging").default;
  messaging().setBackgroundMessageHandler(async () => undefined);
} catch {
  // Firebase credentials are supplied outside the repository.
}

AppRegistry.registerComponent(appName, () => App);
