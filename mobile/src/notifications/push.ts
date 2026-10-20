import { Platform } from "react-native";

export type PushPermission = "granted" | "denied" | "unavailable";

type MessagingModule = {
  (): {
    requestPermission(): Promise<number>;
    getToken(): Promise<string>;
  };
  AuthorizationStatus: { AUTHORIZED: number; PROVISIONAL: number };
};

export async function readPushToken(
  loadMessaging: () => MessagingModule = loadFirebaseMessaging,
): Promise<{ permission: PushPermission; token: string | null }> {
  let messaging: MessagingModule;
  try {
    messaging = loadMessaging();
  } catch {
    return { permission: "unavailable", token: null };
  }
  try {
    const status = await messaging().requestPermission();
    const granted =
      status === messaging.AuthorizationStatus.AUTHORIZED ||
      status === messaging.AuthorizationStatus.PROVISIONAL;
    if (!granted) {
      return { permission: "denied", token: null };
    }
    const token = await messaging().getToken();
    if (!token) {
      return { permission: "denied", token: null };
    }
    return { permission: "granted", token };
  } catch {
    return { permission: "unavailable", token: null };
  }
}

export function currentPlatform(): "ANDROID" | "IOS" {
  return Platform.OS === "ios" ? "IOS" : "ANDROID";
}

function loadFirebaseMessaging(): MessagingModule {
  return require("@react-native-firebase/messaging").default as MessagingModule;
}
