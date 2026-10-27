import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { listReceipts } from "@/features/finance/client";
import {
  getGymProfile,
  getGymSettings,
  updateGymProfile,
  updateGymSettings,
} from "@/features/operations/client";
import { GymSettingsManager } from "@/features/operations/gym-settings-manager";
import { formatAppDateTime } from "@/lib/date";
import type { GymProfile, GymSettings, Receipt } from "@/types/api";

vi.mock("@/features/operations/client", () => ({
  deleteGymLogo: vi.fn(),
  getGymProfile: vi.fn(),
  getGymSettings: vi.fn(),
  updateGymProfile: vi.fn(),
  updateGymSettings: vi.fn(),
  uploadGymLogo: vi.fn(),
}));

vi.mock("@/features/finance/client", () => ({
  listReceipts: vi.fn(),
}));

const loadProfile = vi.mocked(getGymProfile);
const loadSettings = vi.mocked(getGymSettings);
const saveProfile = vi.mocked(updateGymProfile);
const saveSettings = vi.mocked(updateGymSettings);
const loadReceipts = vi.mocked(listReceipts);

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
  has_logo: false,
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
  printer_enabled: false,
  printer_print_logo: false,
  printer_print_address: false,
  printer_print_phone: false,
  printer_footer: "",
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
  canViewReceipts: true,
};

describe("Gym settings manager", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    loadProfile.mockResolvedValue(profile);
    loadSettings.mockResolvedValue(settings);
    saveProfile.mockResolvedValue({ ...profile, name: "Updated Gym" });
    saveSettings.mockResolvedValue(settings);
    loadReceipts.mockResolvedValue({
      data: [],
      meta: { has_more: false, next_after: null },
    });
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
      await screen.findByRole("button", { name: "Edit gym profile" }),
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
        canViewReceipts={false}
      />,
    );

    expect(await screen.findByText("Yangon Fitness")).toBeInTheDocument();
    expect(screen.getByText("SET-A")).toBeInTheDocument();
    expect(screen.getByText("ACTIVE")).toBeInTheDocument();
    expect(screen.getByText("Trial")).toBeInTheDocument();
    expect(screen.getByText("TRIAL")).toBeInTheDocument();
    expect(screen.getByText("Yangon")).toBeInTheDocument();
    expect(
      screen.queryByText("39f07d2b-bbd8-43d2-9c18-e4cbf3af131e"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("af131e")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Operations" }));
    expect(screen.getByText("Monday")).toBeInTheDocument();
    expect(screen.getByText("Required")).toBeInTheDocument();
    expect(screen.getByText("MEM-000041")).toBeInTheDocument();
    const updated = formatAppDateTime("2026-10-02T00:00:00Z");
    expect(screen.getByText(updated)).toBeInTheDocument();
    expect(screen.queryByText("2026-10-02T00:00:00Z")).not.toBeInTheDocument();
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
        canViewReceipts={false}
      />,
    );

    expect(await screen.findByText("Operational settings")).toBeInTheDocument();
    expect(screen.queryByText("Gym profile")).not.toBeInTheDocument();
    expect(loadProfile).not.toHaveBeenCalled();
  });

  it("saves a normalized profile payload", async () => {
    render(<GymSettingsManager {...fullAccess} canViewSettings={false} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Edit gym profile" }),
    );
    expect(screen.getByText("SET-A")).toBeInTheDocument();
    expect(screen.getByText("Trial")).toBeInTheDocument();
    expect(
      screen.queryByRole("textbox", { name: "Code" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("textbox", { name: /status|subscription/i }),
    ).not.toBeInTheDocument();

    expect(screen.getByRole("combobox", { name: "Region" })).toHaveValue("");
    expect(screen.getByRole("combobox", { name: "City" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Updated Gym" },
    });
    fireEvent.change(screen.getByLabelText("Currency"), {
      target: { value: "THB" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save gym profile" }));

    await waitFor(() =>
      expect(saveProfile).toHaveBeenCalledWith({
        name: "Updated Gym",
        legal_name: "Updated Gym",
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
    fireEvent.click(
      await screen.findByRole("button", { name: "Edit operational settings" }),
    );
    const approval = await screen.findByRole("checkbox", {
      name: "Require separate finance approval",
    });

    expect(approval).toBeChecked();
    expect(approval).toBeDisabled();
    expect(screen.getAllByText("41")).toHaveLength(1);
    expect(screen.getByText("MEM-000041")).toBeInTheDocument();
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
    fireEvent.click(await screen.findByRole("tab", { name: "Operations" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Edit operational settings" }),
    );
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

    fireEvent.click(screen.getByRole("tab", { name: "Profile" }));
    fireEvent.click(screen.getByRole("button", { name: "Edit gym profile" }));
    const currency = screen.getByLabelText("Currency");
    expect(currency).toHaveValue("THB");
    expect(currency).toHaveTextContent("MMK");
    expect(currency).not.toHaveTextContent("USD");

    fireEvent.click(screen.getByRole("tab", { name: "Operations" }));
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

  function issuedReceipt(overrides: Partial<Receipt> = {}): Receipt {
    return {
      id: "11111111-1111-4111-8111-111111111111",
      payment_id: "22222222-2222-4222-8222-222222222222",
      receipt_number: "RECEIPT-2026-000041",
      issued_at: "2026-10-27T04:00:00Z",
      gym_code: "SET-A",
      gym_name: "Yangon Fitness",
      gym_address: "1 Main",
      member_number: "MEM-000041",
      member_name: "Ada Lovelace",
      description: "Membership",
      amount: "100.00",
      currency: "MMK",
      method: "CASH",
      business_date: "2026-10-27",
      issuer_name: "Owner",
      status: "ISSUED",
      created_at: "2026-10-27T04:00:00Z",
      ...overrides,
    };
  }

  async function openPrinterEditor() {
    fireEvent.click(await screen.findByRole("tab", { name: "Printer" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Edit printer settings" }),
    );
    return screen.findByLabelText("Receipt footer");
  }

  it("opens the printer editor when the saved footer is missing", async () => {
    loadSettings.mockResolvedValue({
      ...settings,
      printer_footer: undefined as unknown as string,
    });
    render(<GymSettingsManager {...fullAccess} />);
    const footer = await openPrinterEditor();
    expect(footer).toHaveValue("");
  });

  it("saves printer settings without sending operational values", async () => {
    render(<GymSettingsManager {...fullAccess} />);
    const footer = await openPrinterEditor();
    fireEvent.click(screen.getByRole("checkbox", { name: "Enable printer" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Print logo" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Print address" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Print phone" }));
    fireEvent.change(footer, { target: { value: "See you\nagain" } });

    expect(screen.getByText("Characters: 13/120")).toBeInTheDocument();
    expect(screen.getByText("Lines: 2/3")).toBeInTheDocument();
    expect(screen.getByText(/printable width at 72mm/)).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Save printer settings" }),
    );

    await waitFor(() =>
      expect(saveSettings).toHaveBeenCalledWith({
        printer_enabled: true,
        printer_print_logo: true,
        printer_print_address: true,
        printer_print_phone: true,
        printer_footer: "See you\nagain",
      }),
    );
    const payload = saveSettings.mock.calls[0]?.[0];
    expect(payload).not.toHaveProperty("week_starts_on");
    expect(payload).not.toHaveProperty("member_number_prefix");
    expect(payload).not.toHaveProperty("receipt_number_prefix");
    expect(payload).not.toHaveProperty("require_separate_finance_approval");
    expect(payload).not.toHaveProperty("next_member_number");
    expect(
      await screen.findByText("Printer settings saved."),
    ).toBeInTheDocument();
  });

  it("counts Unicode characters and blocks a footer past 120 characters or 3 lines", async () => {
    render(<GymSettingsManager {...fullAccess} />);
    const footer = await openPrinterEditor();

    expect(screen.getByText("Characters: 0/120")).toBeInTheDocument();
    expect(screen.getByText("Lines: 0/3")).toBeInTheDocument();

    fireEvent.change(footer, { target: { value: "😀".repeat(120) } });
    expect(screen.getByText("Characters: 120/120")).toBeInTheDocument();
    expect(
      screen.queryByText("Enter at most 120 characters."),
    ).not.toBeInTheDocument();

    fireEvent.change(footer, { target: { value: "က".repeat(121) } });
    expect(screen.getByText("Characters: 121/120")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Enter at most 120 characters.",
    );
    expect(
      screen.getByRole("button", { name: "Save printer settings" }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole("button", { name: "Save printer settings" }),
    );
    expect(saveSettings).not.toHaveBeenCalled();

    fireEvent.change(footer, { target: { value: "one\ntwo\nthree\n" } });
    expect(screen.getByText("Lines: 3/3")).toBeInTheDocument();
    expect(
      screen.queryByText("Enter at most 3 lines."),
    ).not.toBeInTheDocument();

    fireEvent.change(footer, { target: { value: "a\nb\nc\nd" } });
    expect(screen.getByText("Lines: 4/3")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Enter at most 3 lines.",
    );
    expect(saveSettings).not.toHaveBeenCalled();
  });

  it("links a test slip and the latest issued receipt", async () => {
    const older = issuedReceipt({
      id: "33333333-3333-4333-8333-333333333333",
      receipt_number: "RECEIPT-2026-000001",
      status: "VOID",
      issued_at: "2026-10-28T00:00:00Z",
    });
    const latest = issuedReceipt();
    loadReceipts.mockResolvedValue({
      data: [older, latest],
      meta: { has_more: false, next_after: null },
    });
    render(<GymSettingsManager {...fullAccess} />);
    fireEvent.click(await screen.findByRole("tab", { name: "Printer" }));

    expect(
      await screen.findByRole("link", { name: "Test print" }),
    ).toHaveAttribute("href", "/receipts/test-print");
    expect(screen.getByRole("link", { name: "Test print" })).toHaveClass(
      "button-cancel",
    );
    expect(
      screen.getByRole("link", { name: "Reprint last receipt" }),
    ).not.toHaveClass("button-cancel");
    expect(
      screen.getByRole("link", { name: "Reprint last receipt" }),
    ).toHaveAttribute(
      "href",
      "/receipts/11111111-1111-4111-8111-111111111111/print",
    );
    expect(
      screen.getByText("Last receipt RECEIPT-2026-000041."),
    ).toBeInTheDocument();
    expect(loadReceipts).toHaveBeenCalledWith({
      status: "ISSUED",
      limit: "1",
    });
  });

  it("explains when no issued receipt can be reprinted", async () => {
    render(<GymSettingsManager {...fullAccess} />);
    fireEvent.click(await screen.findByRole("tab", { name: "Printer" }));

    expect(
      await screen.findByText("No issued receipt is available to reprint."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Reprint last receipt" }),
    ).toBeDisabled();
    expect(screen.getByRole("link", { name: "Test print" })).toHaveAttribute(
      "href",
      "/receipts/test-print",
    );
  });

  it("does not request receipts without receipt access", async () => {
    render(<GymSettingsManager {...fullAccess} canViewReceipts={false} />);
    fireEvent.click(await screen.findByRole("tab", { name: "Printer" }));

    expect(screen.getByRole("button", { name: "Test print" })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Reprint last receipt" }),
    ).toBeDisabled();
    expect(
      screen.getByText(
        "You need receipt access before a test slip can be printed.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "You need receipt access before the last receipt can be reprinted.",
      ),
    ).toBeInTheDocument();
    expect(loadReceipts).not.toHaveBeenCalled();
    expect(
      await screen.findByRole("button", { name: "Edit printer settings" }),
    ).toBeInTheDocument();
  });

  it("keeps printer controls read-only without settings management", async () => {
    loadSettings.mockResolvedValue({
      ...settings,
      printer_enabled: true,
      printer_footer: "Thank you",
    });
    render(
      <GymSettingsManager
        canViewProfile
        canUpdateProfile={false}
        canViewSettings
        canManageSettings={false}
        canViewReceipts={false}
      />,
    );
    fireEvent.click(await screen.findByRole("tab", { name: "Printer" }));

    expect(await screen.findByText("Thank you")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Edit printer settings" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("checkbox", { name: "Enable printer" }),
    ).not.toBeInTheDocument();
    expect(loadReceipts).not.toHaveBeenCalled();
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
