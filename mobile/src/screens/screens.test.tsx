import { fireEvent, render } from "@testing-library/react-native";

jest.mock("@react-navigation/native", () => ({
  useIsFocused: () => true,
}));
import type { ReactElement } from "react";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { NetworkError } from "../api/errors";
import type { MemberApi } from "../api/memberApi";
import { MotionProvider } from "../a11y/motion";
import { AppProviders } from "../app/providers";
import { DashboardScreen } from "./DashboardScreen";
import { LoginScreen } from "./LoginScreen";
import { NotificationsScreen } from "./NotificationsScreen";
import { ScanScreen } from "./ScanScreen";

const cameraPermission = () =>
  (
    globalThis as unknown as {
      __cameraPermission: {
        hasPermission: boolean;
        canRequestPermission: boolean;
      };
    }
  ).__cameraPermission;

const metrics = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 0, left: 0, right: 0, bottom: 0 },
};

function renderSignedIn(ui: ReactElement, api: Partial<MemberApi>) {
  return render(
    <SafeAreaProvider initialMetrics={metrics}>
      <MotionProvider>
        <AppProviders sessionStatus="signed-in" api={api as MemberApi}>
          {ui}
        </AppProviders>
      </MotionProvider>
    </SafeAreaProvider>,
  );
}

const dashboardData = {
  member: {
    id: "member-1",
    member_number: "M1",
    first_name: "Ana",
    last_name: "Lee",
    phone: null,
    email: null,
    gender: null,
    date_of_birth: null,
    address: {
      address_line1: "",
      address_line2: "",
      city: "",
      region: "",
      postal_code: "",
      country_code: "",
    },
    join_date: "2026-01-01",
    emergency_contact_name: "",
    emergency_contact_phone: "",
    profile_photo_asset_id: null,
    is_pt_client: false,
    status: "ACTIVE" as const,
    archived_at: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  current_membership: null,
  days_remaining: 12,
  is_pt_client: false,
  membership_history: [],
  membership_history_has_more: false,
  payment_summary: {
    available: true,
    currency: "THB",
    total_charged: "10.00",
    total_refunded: "0.00",
    net_paid: "10.00",
    outstanding_membership_balance: "0.00",
  },
  check_in_summary: {
    available: true,
    currently_checked_in: false,
    open_check_in_id: null,
    last_check_in_at: null,
    last_check_out_at: null,
  },
  gym: {
    name: "North Gym",
    has_logo: false,
  },
};

describe("member screens", () => {
  afterEach(() => {
    cameraPermission().hasPermission = true;
    cameraPermission().canRequestPermission = true;
  });

  it("names the login controls", async () => {
    const view = await render(
      <SafeAreaProvider initialMetrics={metrics}>
        <MotionProvider>
          <AppProviders
            sessionStatus="anonymous"
            auth={{
              status: "anonymous",
              login: jest.fn(async () => undefined),
              logout: jest.fn(async () => ({ serverConfirmed: true })),
              retry: jest.fn(async () => undefined),
            }}
          >
            <LoginScreen />
          </AppProviders>
        </MotionProvider>
      </SafeAreaProvider>,
    );
    expect(
      view.getByRole("button", { name: "Continue with Google" }),
    ).toBeTruthy();
  });

  it("shows a dashboard error and the server payment amount after retry", async () => {
    let fail = true;
    const dashboard = jest.fn(async () => {
      if (fail) {
        throw new NetworkError();
      }
      return dashboardData;
    });
    const view = await renderSignedIn(<DashboardScreen />, { dashboard });
    expect(await view.findByText(/offline/i)).toBeTruthy();
    fail = false;
    fireEvent.press(view.getByRole("button", { name: "Try again" }));
    expect((await view.findAllByText(/THB 10\.00/)).length).toBeGreaterThan(0);
    expect(view.getByText("12")).toBeTruthy();
    expect(view.getByText("Days remaining:")).toBeTruthy();
    expect(view.getByText("North Gym")).toBeTruthy();
    expect(view.getByText("North Gym").props.numberOfLines).toBe(3);
    expect(view.getByLabelText("GymPilot MM")).toBeTruthy();
  });

  it("shows the signed-in gym logo instead of the default wordmark", async () => {
    const gymLogo = jest.fn(async () => ({
      contentType: "image/png",
      bytes: Uint8Array.from([137, 80, 78, 71]),
    }));
    const view = await renderSignedIn(<DashboardScreen />, {
      dashboard: async () => ({
        ...dashboardData,
        gym: { name: "North Gym", has_logo: true },
      }),
      gymLogo,
    });
    expect(await view.findByLabelText("Gym logo")).toBeTruthy();
    expect(view.getByText("North Gym")).toBeTruthy();
    expect(view.queryByText("GymPilot MM")).toBeNull();
    expect(gymLogo).toHaveBeenCalled();
  });

  it("keeps the gym name when the gym logo cannot be loaded", async () => {
    const view = await renderSignedIn(<DashboardScreen />, {
      dashboard: async () => ({
        ...dashboardData,
        gym: { name: "North Gym", has_logo: true },
      }),
      gymLogo: async () => {
        throw new Error("logo failed");
      },
    });
    expect(await view.findByText("North Gym")).toBeTruthy();
    expect(view.getByText("North Gym").props.numberOfLines).toBe(3);
    expect(await view.findByLabelText("GymPilot MM")).toBeTruthy();
    expect(view.queryByLabelText("Gym logo")).toBeNull();
  });

  it("explains desk check-in when the camera is denied", async () => {
    cameraPermission().hasPermission = false;
    cameraPermission().canRequestPermission = false;
    const view = await renderSignedIn(<ScanScreen />, {});
    expect(view.getAllByText(/front desk/i).length).toBeGreaterThan(0);
    expect(view.getAllByText(/system settings/i).length).toBeGreaterThan(0);
  });

  it("shows inbox content and a read action", async () => {
    const notifications = jest.fn(async () => ({
      items: [
        {
          id: "note-1",
          type: "ANNOUNCEMENT" as const,
          title: "Holiday hours",
          body: "The gym closes at 6.",
          announcement_type: "GENERAL" as const,
          read_at: null,
          created_at: "2026-10-20T09:00:00+07:00",
        },
      ],
      meta: { has_more: false, next_after: null },
    }));
    const view = await renderSignedIn(<NotificationsScreen />, {
      notifications,
      markNotificationRead: jest.fn(),
    });
    expect(await view.findByText("Holiday hours")).toBeTruthy();
    expect(view.getByRole("button", { name: "Mark read" })).toBeTruthy();
  });
});
