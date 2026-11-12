jest.mock("@react-navigation/native", () => ({
  useIsFocused: () => true,
  useNavigation: () => ({ canGoBack: () => false, goBack: () => undefined }),
  useRoute: () => ({ params: undefined }),
}));

import { render } from "@testing-library/react-native";
import type { ReactElement } from "react";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { MotionProvider } from "../a11y/motion";
import { AppProviders } from "../app/providers";
import { createSecureStore } from "../auth/secureStore";
import { NotificationSettingsScreen } from "./NotificationSettingsScreen";
import { SettingsScreen } from "./SettingsScreen";

const metrics = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 0, left: 0, right: 0, bottom: 0 },
};

function renderSignedIn(ui: ReactElement) {
  return render(
    <SafeAreaProvider initialMetrics={metrics}>
      <MotionProvider>
        <AppProviders sessionStatus="signed-in" api={{} as never}>
          {ui}
        </AppProviders>
      </MotionProvider>
    </SafeAreaProvider>,
  );
}

describe("saved phone settings", () => {
  beforeEach(async () => {
    const store = createSecureStore();
    await store.resetAll();
    await store.setBiometricGate(true);
    await store.setPushConsent(true);
  });

  it("shows the stored biometric lock after the settings screen mounts", async () => {
    const view = await renderSignedIn(
      <SettingsScreen
        navigation={{ navigate: jest.fn() } as never}
        route={{} as never}
      />,
    );
    expect(await view.findByText("Biometric lock on")).toBeTruthy();
  });

  it("shows stored push consent after the notification screen mounts", async () => {
    const view = await renderSignedIn(<NotificationSettingsScreen />);
    expect(await view.findByText("Turn push off")).toBeTruthy();
  });
});
