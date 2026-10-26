import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TrainersManager } from "@/features/pt/trainers-manager";
import {
  cancelAssignment,
  createAssignment,
  endAssignment,
  listAssignedMembers,
  listAssignments,
  listTrainers,
} from "@/features/pt/client";
import { listMembers } from "@/features/operations/client";

vi.mock("@/features/pt/client", () => ({
  listTrainers: vi.fn(),
  listAssignments: vi.fn(),
  listAssignedMembers: vi.fn(),
  createAssignment: vi.fn(),
  endAssignment: vi.fn(),
  cancelAssignment: vi.fn(),
}));

vi.mock("@/features/operations/client", () => ({
  OperationError: class OperationError extends Error {
    readonly code: string;
    constructor(
      message: string,
      _fields: Record<string, string> = {},
      code = "",
    ) {
      super(message);
      this.code = code;
    }
  },
  listMembers: vi.fn(),
}));

const TRAINER = "11111111-1111-4111-8111-111111111111";
const MEMBER = "22222222-2222-4222-8222-222222222222";
const ASSIGNMENT = "33333333-3333-4333-8333-333333333333";
const empty = { data: [], meta: { has_more: false, next_after: null } };

function page<T>(data: T[]) {
  return { data, meta: { has_more: false, next_after: null } } as never;
}

const trainer = {
  id: TRAINER,
  staff_code: "TR-1",
  display_name: "Ada",
};

const member = {
  id: MEMBER,
  member_number: "M-1",
  first_name: "Grace",
  last_name: "Hopper",
};

function assignment() {
  return {
    id: ASSIGNMENT,
    trainer_staff_profile_id: TRAINER,
    member_id: MEMBER,
    effective_from: "2026-10-01",
    effective_until: null,
    status: "ACTIVE",
    ended_at: null,
    reason: "",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    trainer,
    member,
  };
}

function deferred<T>() {
  let resolve: (value: T | PromiseLike<T>) => void = () => {};
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function renderManager({
  canCreate = true,
  canClose = true,
}: {
  canCreate?: boolean;
  canClose?: boolean;
} = {}) {
  return render(
    <TrainersManager
      canClose={canClose}
      canCreate={canCreate}
      today="2026-10-19"
    />,
  );
}

async function renderReady() {
  renderManager();
  await screen.findByRole("button", { name: "Ada (TR-1)" });
}

async function openCreateDialog() {
  fireEvent.click(screen.getByRole("button", { name: "Create assignment" }));
  return screen.findByRole("dialog", { name: "Create assignment" });
}

async function chooseTrainer(dialog: HTMLElement) {
  await within(dialog).findByRole("option", { name: "Ada (TR-1)" });
  fireEvent.change(within(dialog).getByLabelText("Trainer"), {
    target: { value: TRAINER },
  });
}

async function chooseMember(dialog: HTMLElement) {
  fireEvent.click(
    within(dialog).getByRole("button", { name: "Search members" }),
  );
  fireEvent.click(
    await within(dialog).findByRole("button", { name: "Grace Hopper (M-1)" }),
  );
}

describe("create assignment dialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listTrainers).mockResolvedValue(page([trainer]));
    vi.mocked(listAssignments).mockResolvedValue(empty);
    vi.mocked(listAssignedMembers).mockResolvedValue(empty);
    vi.mocked(listMembers).mockResolvedValue(page([member]));
  });

  it("opens a labelled modal and closes it from cancel, close, escape, and the backdrop", async () => {
    await renderReady();
    expect(
      screen.queryByRole("dialog", { name: "Create assignment" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create assignment" }),
    ).toHaveAttribute("aria-expanded", "false");

    const dialog = await openCreateDialog();
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAttribute(
      "aria-labelledby",
      "create-assignment-title",
    );
    expect(dialog).toHaveAttribute("id", "create-assignment-dialog");
    expect(
      within(dialog).getByRole("heading", { name: "Create assignment" }),
    ).toHaveAttribute("id", "create-assignment-title");
    expect(dialog).toHaveClass(
      "h-full",
      "max-h-full",
      "overflow-y-auto",
      "overscroll-contain",
      "sm:h-auto",
      "sm:max-w-xl",
    );
    expect(dialog.className).toContain("sm:max-h-[min(90dvh,48rem)]");
    expect(dialog.querySelector(".sticky.top-0")).not.toBeNull();
    expect(dialog.querySelector(".sticky.bottom-0")).not.toBeNull();
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeEnabled();
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveClass(
      "w-full",
      "sm:w-auto",
    );
    expect(
      within(dialog).getByRole("button", { name: "Save assignment" }),
    ).toHaveClass("w-full", "sm:w-auto");
    expect(within(dialog).getByLabelText("Effective from")).toHaveValue(
      "2026-10-19",
    );
    expect(within(dialog).getByLabelText("Reason")).toBeInTheDocument();

    const backdrop = dialog.parentElement;
    if (!backdrop) throw new Error("Missing create assignment backdrop.");
    expect(backdrop.tagName).toBe("DIV");
    expect(backdrop).toHaveAttribute("role", "presentation");
    expect(backdrop).toHaveClass(
      "fixed",
      "inset-0",
      "overflow-hidden",
      "sm:place-items-center",
    );
    expect(
      screen.getByRole("heading", { name: "Trainer directory" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Assignments" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create assignment" }),
    ).toHaveAttribute("aria-expanded", "true");
    expect(
      screen.getByRole("button", { name: "Create assignment" }),
    ).toHaveAttribute("aria-controls", "create-assignment-dialog");

    fireEvent.mouseDown(within(dialog).getByLabelText("Reason"));
    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(screen.getByRole("dialog", { name: "Create assignment" })).toBe(
      dialog,
    );

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(
      screen.queryByRole("dialog", { name: "Create assignment" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create assignment" }),
    ).toHaveAttribute("aria-expanded", "false");

    const closed = await openCreateDialog();
    fireEvent.click(within(closed).getByRole("button", { name: "Close" }));
    expect(
      screen.queryByRole("dialog", { name: "Create assignment" }),
    ).not.toBeInTheDocument();

    const escaped = await openCreateDialog();
    fireEvent.keyDown(escaped, { key: "Escape" });
    expect(
      screen.queryByRole("dialog", { name: "Create assignment" }),
    ).not.toBeInTheDocument();

    const dismissed = await openCreateDialog();
    const dismissedBackdrop = dismissed.parentElement;
    if (!dismissedBackdrop)
      throw new Error("Missing create assignment backdrop.");
    fireEvent.mouseDown(dismissedBackdrop);
    expect(
      screen.queryByRole("dialog", { name: "Create assignment" }),
    ).not.toBeInTheDocument();
  });

  it("keeps validation and request errors inside the open dialog", async () => {
    await renderReady();
    const dialog = await openCreateDialog();
    await chooseTrainer(dialog);
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save assignment" }),
    );

    const validation = within(dialog).getByRole("alert");
    expect(validation).toHaveTextContent("Select an active member.");
    expect(dialog).toContainElement(validation);
    expect(screen.getAllByRole("alert")).toEqual([validation]);
    expect(createAssignment).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "Create assignment" })).toBe(
      dialog,
    );

    await chooseMember(dialog);
    vi.mocked(createAssignment).mockRejectedValue(
      new Error("Trainer is unavailable."),
    );
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save assignment" }),
    );
    const failure = await within(dialog).findByRole("alert");
    expect(failure).toHaveTextContent("Trainer is unavailable.");
    expect(dialog).toContainElement(failure);
    expect(screen.getAllByRole("alert")).toEqual([failure]);
    expect(screen.getByRole("dialog", { name: "Create assignment" })).toBe(
      dialog,
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(
      screen.queryByRole("dialog", { name: "Create assignment" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Trainer is unavailable."),
    ).not.toBeInTheDocument();
  });

  it("ignores dismissals while saving and closes after the assignment is created", async () => {
    const pendingCreate = deferred<never>();
    vi.mocked(createAssignment).mockImplementation(
      () => pendingCreate.promise as never,
    );
    await renderReady();
    fireEvent.click(screen.getByRole("button", { name: "Ada (TR-1)" }));
    await waitFor(() => expect(listAssignedMembers).toHaveBeenCalled());
    const assignmentsBefore = vi.mocked(listAssignments).mock.calls.length;
    const assignedBefore = vi.mocked(listAssignedMembers).mock.calls.length;

    const dialog = await openCreateDialog();
    await chooseTrainer(dialog);
    await chooseMember(dialog);
    fireEvent.change(within(dialog).getByLabelText("Effective from"), {
      target: { value: "2026-10-20" },
    });
    fireEvent.change(within(dialog).getByLabelText("Effective until"), {
      target: { value: "2026-11-01" },
    });
    fireEvent.change(within(dialog).getByLabelText("Reason"), {
      target: { value: "New client" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save assignment" }),
    );

    await waitFor(() => expect(createAssignment).toHaveBeenCalledTimes(1));
    expect(createAssignment).toHaveBeenCalledWith({
      trainer_staff_profile_id: TRAINER,
      member_id: MEMBER,
      effective_from: "2026-10-20",
      effective_until: "2026-11-01",
      reason: "New client",
    });
    expect(dialog).toHaveAttribute("aria-busy", "true");
    expect(
      within(dialog).getByRole("button", { name: "Close" }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "Cancel" }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "Save assignment" }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "Search members" }),
    ).toBeDisabled();

    fireEvent.keyDown(dialog, { key: "Escape" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    fireEvent.mouseDown(dialog.parentElement ?? dialog);
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save assignment" }),
    );
    expect(screen.getByRole("dialog", { name: "Create assignment" })).toBe(
      dialog,
    );
    expect(createAssignment).toHaveBeenCalledTimes(1);

    await act(async () => {
      pendingCreate.resolve({} as never);
      await pendingCreate.promise;
    });
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Create assignment" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("status")).toHaveTextContent("Assignment created.");
    await waitFor(() =>
      expect(vi.mocked(listAssignments).mock.calls.length).toBeGreaterThan(
        assignmentsBefore,
      ),
    );
    await waitFor(() =>
      expect(vi.mocked(listAssignedMembers).mock.calls.length).toBeGreaterThan(
        assignedBefore,
      ),
    );
  });

  it("opens end and cancel assignment forms in dialogs", async () => {
    vi.mocked(listAssignments).mockResolvedValue(page([assignment()]));
    vi.mocked(endAssignment).mockRejectedValue(new Error("End failed."));
    renderManager();

    fireEvent.click(
      await screen.findByRole("button", { name: "End assignment" }),
    );
    const endForm = screen.getByRole("dialog", { name: "End assignment" });
    expect(endForm).toHaveAttribute("aria-modal", "true");
    expect(endForm.parentElement).toHaveClass("fixed");
    expect(
      within(endForm).queryByRole("button", { name: "Close" }),
    ).not.toBeInTheDocument();
    expect(
      within(endForm).queryByRole("button", { name: "Cancel" }),
    ).not.toBeInTheDocument();

    fireEvent.change(within(endForm).getByLabelText("Reason for ending"), {
      target: { value: "Finished" },
    });
    fireEvent.click(
      within(endForm).getByRole("button", { name: "Confirm end" }),
    );
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("End failed.");
    expect(endForm).not.toContainElement(alert);
    expect(screen.getByRole("dialog", { name: "End assignment" })).toBe(
      endForm,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel assignment" }));
    const cancelForm = screen.getByRole("dialog", {
      name: "Cancel assignment",
    });
    expect(cancelForm).toHaveAttribute("aria-modal", "true");
    expect(cancelForm.parentElement).toHaveClass("fixed");
    expect(
      within(cancelForm).getByRole("button", { name: "Confirm cancel" }),
    ).toBeInTheDocument();
    expect(
      within(cancelForm).queryByRole("button", { name: "Close" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("dialog", { name: "End assignment" }),
    ).not.toBeInTheDocument();
    expect(cancelAssignment).not.toHaveBeenCalled();
  });

  it("does not offer the create dialog without permission", async () => {
    renderManager({ canCreate: false, canClose: false });
    await screen.findByRole("button", { name: "Ada (TR-1)" });
    expect(
      screen.queryByRole("button", { name: "Create assignment" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("dialog", { name: "Create assignment" }),
    ).not.toBeInTheDocument();
  });
});
