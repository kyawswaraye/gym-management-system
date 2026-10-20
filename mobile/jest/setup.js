jest.mock("react-native-gesture-handler", () => {
  const View = require("react-native").View;
  return {
    GestureHandlerRootView: View,
    Swipeable: View,
    DrawerLayout: View,
    State: {},
    PanGestureHandler: View,
    TapGestureHandler: View,
    ScrollView: require("react-native").ScrollView,
  };
});

jest.mock("react-native-screens", () => {
  const View = require("react-native").View;
  return {
    enableScreens: jest.fn(),
    screensEnabled: () => false,
    Screen: View,
    ScreenContainer: View,
    NativeScreen: View,
    NativeScreenContainer: View,
  };
});

jest.mock("@react-native-community/netinfo", () => ({
  addEventListener: jest.fn(() => jest.fn()),
  fetch: jest.fn(async () => ({
    isConnected: true,
    isInternetReachable: true,
  })),
}));

jest.mock("react-native-keychain", () => {
  const items = new Map();
  return {
    ACCESSIBLE: {
      WHEN_UNLOCKED_THIS_DEVICE_ONLY: "AccessibleWhenUnlockedThisDeviceOnly",
    },
    ACCESS_CONTROL: {
      BIOMETRY_CURRENT_SET_OR_DEVICE_PASSCODE:
        "BiometryCurrentSetOrDevicePasscode",
    },
    SECURITY_LEVEL: { SECURE_HARDWARE: 1, SECURE_SOFTWARE: 0, ANY: 2 },
    setGenericPassword: jest.fn(async (_username, password, options) => {
      items.set(options.service, password);
      return { service: options.service };
    }),
    getGenericPassword: jest.fn(async (options) => {
      const password = items.get(options.service);
      return password ? { username: "item", password } : false;
    }),
    resetGenericPassword: jest.fn(async (options) => {
      items.delete(options.service);
      return true;
    }),
  };
});

globalThis.__cameraPermission = {
  hasPermission: true,
  canRequestPermission: true,
  requestPermission: jest.fn(async () => true),
  status: "authorized",
};

jest.mock("react-native-vision-camera", () => {
  const View = require("react-native").View;
  return {
    Camera: View,
    useCameraDevice: () => ({ id: "back", position: "back" }),
    useCameraPermission: () => globalThis.__cameraPermission,
    useObjectOutput: () => ({ id: "codes" }),
    isScannedCode: (object) => typeof object?.value === "string",
  };
});

jest.mock("@react-native-firebase/app", () => ({}));
jest.mock("@react-native-firebase/messaging", () => {
  const messaging = () => ({
    requestPermission: jest.fn(async () => 1),
    getToken: jest.fn(async () => "test-push-token"),
    setBackgroundMessageHandler: jest.fn(),
  });
  messaging.AuthorizationStatus = { AUTHORIZED: 1, PROVISIONAL: 2 };
  return messaging;
});

jest.mock("react-native-image-picker", () => ({
  launchImageLibrary: jest.fn(async () => ({ assets: [] })),
  launchCamera: jest.fn(async () => ({ assets: [] })),
}));
