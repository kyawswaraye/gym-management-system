import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  getGymProfile,
  getGymSettings,
  updateGymProfile,
  updateGymSettings,
} from "@/features/operations/client";
import { GymSettingsManager } from "@/features/operations/gym-settings-manager";
import type { GymProfile, GymSettings } from "@/types/api";

vi.mock("@/features/operations/client", () => ({
  getGymProfile: vi.fn(),
  getGymSettings: vi.fn(),
  updateGymProfile: vi.fn(),
  updateGymSettings: vi.fn(),
}));

const loadProfile = vi.mocked(getGymProfile);
const loadSettings = vi.mocked(getGymSettings);
const saveProfile = vi.mocked(updateGymProfile);
const saveSettings = vi.mocked(updateGymSettings);

const profile: GymProfile = {
  id: "f52030c1-18af-4e18-a1ec-50997d892ef4",
  code: "SET-A",
  name: "Yangon Fitness",
  legal_name: "Yangon Fitness Co",
  status: "ACTIVE",
  email: "gym@example.test",
  phone: "01",
  address_line1: "1 Main",
  address_line2: "",
  city: "Yangon",
  region: "",
  postal_code: "",
  country_code: "MM",
  timezone: "Asia/Yangon",
  currency: "THB",
  subscription_plan_id: "39f07d2b-bbd8-43d2-9c18-e4cbf3af131e",
  subscription_plan_name: "Trial",
  subscription_status: "TRIAL",
  subscription_starts_at: null,
  subscription_ends_at: null,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-02T00:00:00Z",
};

const settings: GymSettings = {
  gym_id: "f52030c1-18af-4e18-a1ec-50997d892ef4",
  week_starts_on: 1,
  allow_manual_member_numbers: false,
  member_number_prefix: "MEM",
  receipt_number_prefix: "RECEIPT",
  check_in_duplicate_window_seconds: 300,
  check_in_challenge_ttl_seconds: 120,
  check_in_challenge_max_uses: 1,
  membership_expiry_warning_days: [3, 7],
  membership_expiry_email_enabled: true,
  membership_expiry_push_enabled: false,
  class_cancellation_cutoff_minutes: 60,
  class_no_show_consumes_session: true,
  require_separate_finance_approval: true,
  next_member_number: 41,
  created_by_membership_id: "3a944485-5df4-449a-9877-e4b91d41ee52",
  updated_by_membership_id: "3a944485-5df4-449a-9877-e4b91d41ee52",
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-02T00:00:00Z",
};

const fullAccess = {
  canViewProfile: true,
  canUpdateProfile: true,
  canViewSettings: true,
  canManageSettings: true,
};

describe("Gym settings manager", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    loadProfile.mockResolvedValue(profile);
    loadSettings.mockResolvedValue(settings);
    saveProfile.mockResolvedValue({ ...profile, name: "Updated Gym" });
    saveSettings.mockResolvedValue(settings);
  });

  it("shows a loading status and then the editable profile", async () => {
    let resolveProfile: (value: GymProfile) => void = () => undefined;
    loadProfile.mockReturnValue(
      new Promise((resolve) => {
        resolveProfile = resolve;
      }),
    );
    render(<GymSettingsManager {...fullAccess} canViewSettings={false} />);

    expect(screen.getByRole("status")).toHaveTextContent(
      "Loading gym profile.",
    );
    resolveProfile(profile);

    expect(
      await screen.findByRole("button", { name: "Save gym profile" }),
    ).toBeInTheDocument();
    expect(loadSettings).not.toHaveBeenCalled();
  });

  it("renders read-only data and hides unauthorized controls", async () => {
    render(
      <GymSettingsManager
        canViewProfile
        canUpdateProfile={false}
        canViewSettings
        canManageSettings={false}
      />,
    );

    expect(await screen.findByText("Yangon Fitness")).toBeInTheDocument();
    expect(screen.getByText("SET-A")).toBeInTheDocument();
    expect(screen.getByText("ACTIVE")).toBeInTheDocument();
    expect(screen.getByText("Trial")).toBeInTheDocument();
    expect(screen.getByText("TRIAL")).toBeInTheDocument();
    expect(
      screen.getByTitle("39f07d2b-bbd8-43d2-9c18-e4cbf3af131e"),
    ).toHaveTextContent("af131e");
    expect(screen.getByText("Monday")).toBeInTheDocument();
    expect(screen.getByText("Required")).toBeInTheDocument();
    expect(screen.getByText("41")).toBeInTheDocument();
    expect(screen.getByText("2026-10-02T00:00:00Z")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Save gym profile" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Save operational settings" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("checkbox", {
        name: "Require separate finance approval",
      }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /suspend|activate/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("textbox", { name: "Code" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("textbox", { name: /status|subscription/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("combobox", { name: /status|subscription/i }),
    ).not.toBeInTheDocument();
  });

  it("hides sections the user cannot view", async () => {
    render(
      <GymSettingsManager
        canViewProfile={false}
        canUpdateProfile={false}
        canViewSettings
        canManageSettings={false}
      />,
    );

    expect(await screen.findByText("Operational settings")).toBeInTheDocument();
    expect(screen.queryByText("Gym profile")).not.toBeInTheDocument();
    expect(loadProfile).not.toHaveBeenCalled();
  });

  it("saves a normalized profile payload", async () => {
    render(<GymSettingsManager {...fullAccess} canViewSettings={false} />);
    expect(
      await screen.findByRole("button", { name: "Save gym profile" }),
    ).toBeInTheDocument();
    expect(screen.getByText("SET-A")).toBeInTheDocument();
    expect(screen.getByText("Trial")).toBeInTheDocument();
    expect(
      screen.queryByRole("textbox", { name: "Code" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("textbox", { name: /status|subscription/i }),
    ).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Updated Gym" },
    });
    fireEvent.change(screen.getByLabelText("Currency"), {
      target: { value: "thb" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save gym profile" }));

    await waitFor(() =>
      expect(saveProfile).toHaveBeenCalledWith({
        name: "Updated Gym",
        legal_name: "Yangon Fitness Co",
        email: "gym@example.test",
        phone: "01",
        address_line1: "1 Main",
        address_line2: "",
        city: "Yangon",
        region: "",
        postal_code: "",
        country_code: "MM",
        timezone: "Asia/Yangon",
        currency: "THB",
      }),
    );
    const payload = saveProfile.mock.calls[0]?.[0];
    expect(payload).not.toHaveProperty("id");
    expect(payload).not.toHaveProperty("code");
    expect(payload).not.toHaveProperty("status");
    expect(payload).not.toHaveProperty("subscription_plan_id");
    expect(payload).not.toHaveProperty("subscription_plan_name");
    expect(payload).not.toHaveProperty("subscription_status");
    expect(payload).not.toHaveProperty("subscription_starts_at");
    expect(payload).not.toHaveProperty("subscription_ends_at");
    expect(payload).not.toHaveProperty("created_at");
    expect(payload).not.toHaveProperty("updated_at");
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Gym profile saved.",
    );
  });

  it("keeps finance approval mandatory and omits read-only settings fields", async () => {
    render(<GymSettingsManager {...fullAccess} canViewProfile={false} />);
    const approval = await screen.findByRole("checkbox", {
      name: "Require separate finance approval",
    });

    expect(approval).toBeChecked();
    expect(approval).toBeDisabled();
    expect(screen.getByText("41")).toBeInTheDocument();
    expect(
      screen.queryByRole("spinbutton", { name: "Next member number" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("textbox", { name: /created|updated/i }),
    ).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Save operational settings" }),
    );

    await waitFor(() =>
      expect(saveSettings).toHaveBeenCalledWith({
        week_starts_on: 1,
        allow_manual_member_numbers: false,
        member_number_prefix: "MEM",
        receipt_number_prefix: "RECEIPT",
        check_in_duplicate_window_seconds: 300,
        check_in_challenge_ttl_seconds: 120,
        check_in_challenge_max_uses: 1,
        membership_expiry_warning_days: [3, 7],
        membership_expiry_email_enabled: true,
        membership_expiry_push_enabled: false,
        class_cancellation_cutoff_minutes: 60,
        class_no_show_consumes_session: true,
        require_separate_finance_approval: true,
      }),
    );
    expect(saveSettings.mock.calls[0]?.[0]).not.toHaveProperty(
      "next_member_number",
    );
    expect(saveSettings.mock.calls[0]?.[0]).not.toHaveProperty(
      "created_by_membership_id",
    );
    expect(saveSettings.mock.calls[0]?.[0]).not.toHaveProperty("updated_at");
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Operational settings saved.",
    );
  });

  it("shows validation and upstream errors without saving invalid settings", async () => {
    render(<GymSettingsManager {...fullAccess} />);
    const prefix = await screen.findByLabelText("Member number prefix");
    fireEvent.change(prefix, { target: { value: "bad prefix" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Save operational settings" }),
    );

    expect(
      await screen.findByText(
        "Member number prefix must be 1 to 20 uppercase letters, digits, or hyphens.",
      ),
    ).toBeInTheDocument();
    expect(saveSettings).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Currency"), {
      target: { value: "US" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save gym profile" }));
    expect(
      await screen.findByText("Enter a three-letter currency code."),
    ).toBeInTheDocument();
    expect(saveProfile).not.toHaveBeenCalled();

    fireEvent.change(prefix, { target: { value: "MEM" } });
    saveSettings.mockRejectedValueOnce(
      new Error(
        "Separate finance approval is required and cannot be disabled.",
      ),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Save operational settings" }),
    );
    expect(
      await screen.findByText(
        "Separate finance approval is required and cannot be disabled.",
      ),
    ).toBeInTheDocument();
  });

  it("reports a profile load failure", async () => {
    loadProfile.mockRejectedValueOnce(
      new Error("Unable to load the gym profile."),
    );
    render(<GymSettingsManager {...fullAccess} canViewSettings={false} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Unable to load the gym profile.",
    );
    expect(
      screen.queryByRole("button", { name: "Save gym profile" }),
    ).not.toBeInTheDocument();
  });
});
