import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { issueCheckInChallenge } from "@/features/operations/client";
import {
  CheckInDisplay,
  nextChallengeRotationMs,
} from "@/features/operations/check-in-display";

vi.mock("qrcode", () => ({
  default: {
    toDataURL: vi.fn(async () => "data:image/png;base64,qr"),
  },
}));

vi.mock("@/features/operations/client", () => ({
  OperationError: class OperationError extends Error {},
  issueCheckInChallenge: vi.fn(),
}));

describe("Check-in QR display", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("rotates before expiry and does not store the plaintext", () => {
    expect(
      nextChallengeRotationMs(
        "2026-10-17T12:00:30.000Z",
        Date.parse("2026-10-17T12:00:00.000Z"),
      ),
    ).toBe(25_000);
    expect(
      nextChallengeRotationMs(
        "2026-10-17T12:00:04.000Z",
        Date.parse("2026-10-17T12:00:00.000Z"),
      ),
    ).toBe(0);
  });

  it("shows a code and replaces it before expiry", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    vi.mocked(issueCheckInChallenge).mockImplementation(async () => ({
      id: "22222222-2222-4222-8222-222222222222",
      payload: "gms-checkin:v1:secret-value",
      display_location: "Front desk",
      issued_at: new Date().toISOString(),
      expires_at: new Date(Date.now() + 20_000).toISOString(),
      max_uses: 1,
      status: "ACTIVE",
    }));

    render(<CheckInDisplay />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(
      screen.getByRole("img", { name: "Current onsite check-in code" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("gms-checkin:v1:secret-value"),
    ).not.toBeInTheDocument();
    expect(setItem).not.toHaveBeenCalled();
    expect(issueCheckInChallenge).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    expect(issueCheckInChallenge).toHaveBeenCalledTimes(2);
  });
});
