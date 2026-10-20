import { NativeModules, Platform } from "react-native";

type ScreenSecurityModule = {
  setEnabled(enabled: boolean): void;
};

export function setScreenSecure(enabled: boolean): void {
  if (Platform.OS !== "android") {
    return;
  }
  const module = NativeModules.ScreenSecurity as
    ScreenSecurityModule | undefined;
  module?.setEnabled(enabled);
}
