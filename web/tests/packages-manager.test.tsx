import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PackagesManager } from "@/features/pt/packages-manager";
import {
  createServicePackage,
  listClassTypes,
  listServicePackages,
} from "@/features/pt/client";

vi.mock("@/features/pt/client", () => ({
  listServicePackages: vi.fn(),
  createServicePackage: vi.fn(),
  listClassTypes: vi.fn(),
}));

const PT_ID = "11111111-1111-4111-8111-111111111111";
const CLASS_ID = "22222222-2222-4222-8222-222222222222";
const CLASS_TYPE = "77777777-7777-4777-8777-777777777777";
const empty = { data: [], meta: { has_more: false, next_after: null } };

function page<T>(data: T[], next: string | null = null) {
  return {
    data,
    meta: { has_more: next !== null, next_after: next },
  } as never;
}

function deferred<T>() {
  let resolve: (value: T | PromiseLike<T>) => void = () => {};
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

const ptPackage = {
  id: PT_ID,
  type: "PT",
  code: "PT10",
  name: "Ten sessions",
  session_count: 10,
  validity_value: 3,
  validity_unit: "MONTH",
  price: "100.00",
  currency: "MMK",
  class_type_id: null,
  is_active: true,
};

const classPackage = {
  id: CLASS_ID,
  type: "CLASS",
  code: "YOGA8",
  name: "Yoga intro",
  session_count: 8,
  validity_value: 1,
  validity_unit: "WEEK",
  price: "0.00",
  currency: "MMK",
  class_type_id: CLASS_TYPE,
  is_active: false,
};

function renderManager(canManage = true) {
  return render(<PackagesManager canManage={canManage} currency="MMK" />);
}

async function openCreateDialog() {
  fireEvent.click(screen.getByRole("button", { name: "Create package" }));
  return screen.findByRole("dialog", { name: "Create package" });
}

function fillPackage(dialog: HTMLElement, code = "PT10") {
  fireEvent.change(within(dialog).getByLabelText("Code"), {
    target: { value: code },
  });
  fireEvent.change(within(dialog).getByLabelText("Name"), {
    target: { value: "Ten sessions" },
  });
  fireEvent.change(within(dialog).getByLabelText("Sessions"), {
    target: { value: "10" },
  });
  fireEvent.change(within(dialog).getByLabelText("Validity"), {
    target: { value: "3" },
  });
  fireEvent.change(within(dialog).getByLabelText("Price"), {
    target: { value: "100.00" },
  });
}

describe("package catalog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listServicePackages).mockResolvedValue(empty);
    vi.mocked(listClassTypes).mockResolvedValue(empty);
  });

  it("renders type, status, sessions, validity, and price for each package", async () => {
    vi.mocked(listServicePackages).mockResolvedValue(
      page([ptPackage, classPackage]),
    );
    renderManager();

    const catalog = await screen.findByRole("region", {
      name: "Service packages",
    });
    expect(
      within(catalog).getByRole("heading", { name: "Service packages" }),
    ).toBeInTheDocument();
    expect(within(catalog).getByText(/stay unchanged/)).toBeInTheDocument();

    const packages = await within(catalog).findAllByRole("listitem");
    expect(packages).toHaveLength(2);
    expect(packages[0]).toHaveTextContent("Ten sessions");
    expect(packages[0]).toHaveTextContent("PT10");
    expect(packages[0]).toHaveTextContent("PT");
    expect(packages[0]).toHaveTextContent("Active");
    expect(packages[0]).toHaveTextContent("10");
    expect(packages[0]).toHaveTextContent("3 months");
    expect(packages[0]).toHaveTextContent("100.00");
    expect(packages[0]).toHaveTextContent("MMK");
    expect(packages[0]).toHaveClass("border-l-amber-400", "bg-white");
    expect(within(packages[0]!).getByText("PT")).toHaveClass(
      "bg-amber-100",
      "text-amber-800",
    );
    expect(within(packages[0]!).getByText("Active")).toHaveClass(
      "bg-emerald-100",
      "text-emerald-700",
    );

    expect(packages[1]).toHaveTextContent("Yoga intro");
    expect(packages[1]).toHaveTextContent("CLASS");
    expect(packages[1]).toHaveTextContent("Inactive");
    expect(packages[1]).toHaveTextContent("1 week");
    expect(packages[1]).toHaveTextContent("0.00");
    expect(packages[1]).toHaveClass("border-l-sky-500", "bg-slate-50");
    expect(within(packages[1]!).getByText("CLASS")).toHaveClass(
      "bg-sky-100",
      "text-sky-800",
    );
    expect(within(packages[1]!).getByText("Inactive")).toHaveClass(
      "bg-slate-200",
      "text-slate-600",
    );
    expect(within(catalog).getByRole("list")).toHaveClass(
      "grid-cols-1",
      "sm:grid-cols-2",
      "xl:grid-cols-3",
    );
  });

  it("filters the catalog and clears those filters", async () => {
    vi.mocked(listServicePackages).mockImplementation(async (query) => {
      if (query?.type === "CLASS") return page([classPackage]);
      if (query?.type === "PT" && query.is_active === "false") return page([]);
      if (query?.type === "PT") return page([ptPackage]);
      return page([ptPackage, classPackage]);
    });
    renderManager();
    expect(await screen.findByText("Yoga intro")).toBeInTheDocument();
    await waitFor(() =>
      expect(listServicePackages).toHaveBeenCalledWith({ limit: "25" }),
    );

    fireEvent.change(
      screen.getByRole("combobox", { name: "Filter by package type" }),
      { target: { value: "PT" } },
    );
    await waitFor(() =>
      expect(listServicePackages).toHaveBeenLastCalledWith({
        limit: "25",
        type: "PT",
      }),
    );
    await waitFor(() =>
      expect(screen.queryByText("Yoga intro")).not.toBeInTheDocument(),
    );
    expect(screen.getByText("Ten sessions")).toBeInTheDocument();

    fireEvent.change(
      screen.getByRole("combobox", { name: "Filter by status" }),
      {
        target: { value: "false" },
      },
    );
    await waitFor(() =>
      expect(listServicePackages).toHaveBeenLastCalledWith({
        limit: "25",
        type: "PT",
        is_active: "false",
      }),
    );
    expect(await screen.findByText("No packages match.")).toBeInTheDocument();
    expect(
      screen.getByText("Try changing or clearing the current filters."),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() =>
      expect(listServicePackages).toHaveBeenLastCalledWith({ limit: "25" }),
    );
    expect(await screen.findByText("Yoga intro")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Clear filters" }),
    ).not.toBeInTheDocument();
  });

  it("shows an empty catalog, then a load failure with retry", async () => {
    renderManager(false);
    expect(await screen.findByText("No packages match.")).toBeInTheDocument();
    expect(
      screen.getByText("Packages will appear here when they are added."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Create package" }),
    ).not.toBeInTheDocument();
    cleanup();

    vi.mocked(listServicePackages).mockRejectedValueOnce(
      new Error("The catalog could not be loaded."),
    );
    renderManager();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The catalog could not be loaded.",
    );
    expect(screen.queryByText("No packages match.")).not.toBeInTheDocument();
    vi.mocked(listServicePackages).mockResolvedValue(page([ptPackage]));
    fireEvent.click(
      screen.getByRole("button", { name: "Retry loading packages" }),
    );
    expect(await screen.findByText("Ten sessions")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a loading state before the first page arrives", async () => {
    const pendingList = deferred<never>();
    vi.mocked(listServicePackages).mockReturnValue(
      pendingList.promise as never,
    );
    renderManager();
    expect(screen.getByRole("status")).toHaveTextContent("Loading packages.");
    expect(screen.queryByText("No packages match.")).not.toBeInTheDocument();
    await waitFor(() => expect(listServicePackages).toHaveBeenCalled());
    await act(async () => {
      pendingList.resolve(page([ptPackage]));
      await pendingList.promise;
    });
    expect(await screen.findByText("Ten sessions")).toBeInTheDocument();
  });

  it("pages the catalog from the card footer", async () => {
    vi.mocked(listServicePackages).mockImplementation(async (query) =>
      query?.after ? page([classPackage]) : page([ptPackage], "package-2"),
    );
    renderManager();
    expect(await screen.findByText("Ten sessions")).toBeInTheDocument();
    const catalog = screen.getByRole("region", { name: "Service packages" });
    const list = within(catalog).getByRole("list");
    const paging = within(catalog).getByRole("navigation", {
      name: "Pagination",
    });
    expect(
      list.compareDocumentPosition(paging) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    fireEvent.click(within(paging).getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("Yoga intro")).toBeInTheDocument();
    await waitFor(() =>
      expect(listServicePackages).toHaveBeenLastCalledWith({
        limit: "25",
        after: "package-2",
      }),
    );
  });
});

describe("create package dialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listServicePackages).mockResolvedValue(page([ptPackage]));
    vi.mocked(listClassTypes).mockResolvedValue(
      page([{ id: CLASS_TYPE, code: "YOGA", name: "Yoga", is_active: true }]),
    );
  });

  it("opens a labelled modal and closes from cancel, close, escape, and the backdrop", async () => {
    renderManager();
    await screen.findByText("Ten sessions");
    expect(
      screen.queryByRole("dialog", { name: "Create package" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create package" }),
    ).toHaveAttribute("aria-expanded", "false");

    const dialog = await openCreateDialog();
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAttribute("aria-labelledby", "create-package-title");
    expect(dialog).toHaveAttribute(
      "aria-describedby",
      "create-package-description",
    );
    expect(dialog).toHaveAttribute("id", "create-package-dialog");
    expect(
      within(dialog).getByRole("heading", { name: "Create package" }),
    ).toHaveAttribute("id", "create-package-title");
    expect(dialog).toHaveClass(
      "h-full",
      "max-h-full",
      "overflow-y-auto",
      "overscroll-contain",
      "sm:h-auto",
      "sm:max-w-2xl",
    );
    expect(dialog.className).toContain("sm:max-h-[min(90dvh,48rem)]");
    expect(dialog.querySelector(".sticky.top-0")).not.toBeNull();
    expect(dialog.querySelector(".sticky.bottom-0")).not.toBeNull();
    expect(within(dialog).getByText(/stays unchanged/)).toBeInTheDocument();
    expect(within(dialog).getByText(/charged in MMK/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeEnabled();
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveClass(
      "w-full",
      "sm:w-auto",
    );
    expect(
      within(dialog).getByRole("button", { name: "Save package" }),
    ).toHaveClass("button-create", "w-full", "sm:w-auto");
    expect(
      within(dialog).queryByRole("combobox", {
        name: "Class type for a CLASS package",
      }),
    ).not.toBeInTheDocument();
    expect(listClassTypes).not.toHaveBeenCalled();

    const backdrop = dialog.parentElement;
    if (!backdrop) throw new Error("Missing create package backdrop.");
    expect(backdrop).toHaveAttribute("role", "presentation");
    expect(backdrop).toHaveClass(
      "fixed",
      "inset-0",
      "overflow-hidden",
      "sm:place-items-center",
    );
    expect(
      screen.getByRole("button", { name: "Create package", hidden: true }),
    ).toHaveAttribute("aria-expanded", "true");
    expect(
      screen.getByRole("button", { name: "Create package", hidden: true }),
    ).toHaveAttribute("aria-controls", "create-package-dialog");

    fireEvent.mouseDown(within(dialog).getByLabelText("Code"));
    expect(screen.getByRole("dialog", { name: "Create package" })).toBe(dialog);

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(
      screen.queryByRole("dialog", { name: "Create package" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create package" }),
    ).toHaveAttribute("aria-expanded", "false");

    const closed = await openCreateDialog();
    fireEvent.click(within(closed).getByRole("button", { name: "Close" }));
    expect(
      screen.queryByRole("dialog", { name: "Create package" }),
    ).not.toBeInTheDocument();

    const escaped = await openCreateDialog();
    fireEvent.keyDown(escaped, { key: "Escape" });
    expect(
      screen.queryByRole("dialog", { name: "Create package" }),
    ).not.toBeInTheDocument();

    const dismissed = await openCreateDialog();
    const dismissedBackdrop = dismissed.parentElement;
    if (!dismissedBackdrop) throw new Error("Missing create package backdrop.");
    fireEvent.mouseDown(dismissedBackdrop);
    expect(
      screen.queryByRole("dialog", { name: "Create package" }),
    ).not.toBeInTheDocument();
  });

  it("requires a class type only for CLASS packages and keeps errors in the dialog", async () => {
    renderManager();
    await screen.findByText("Ten sessions");
    const dialog = await openCreateDialog();
    fillPackage(dialog, "pt10");
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save package" }),
    );
    const codeError = within(dialog).getByRole("alert");
    expect(codeError).toHaveTextContent("Enter an uppercase package code.");
    expect(dialog).toContainElement(codeError);
    expect(screen.getAllByRole("alert")).toEqual([codeError]);
    expect(createServicePackage).not.toHaveBeenCalled();

    fillPackage(dialog);
    fireEvent.change(within(dialog).getByRole("combobox", { name: "Type" }), {
      target: { value: "CLASS" },
    });
    expect(
      await within(dialog).findByRole("option", { name: "YOGA · Yoga" }),
    ).toBeInTheDocument();
    expect(listClassTypes).toHaveBeenCalledWith({
      limit: "100",
      is_active: "true",
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save package" }),
    );
    const classError = within(dialog).getByRole("alert");
    expect(classError).toHaveTextContent(
      "A class package requires a class type.",
    );
    expect(dialog).toContainElement(classError);
    expect(createServicePackage).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "Create package" })).toBe(dialog);

    fireEvent.change(
      within(dialog).getByRole("combobox", {
        name: "Class type for a CLASS package",
      }),
      { target: { value: CLASS_TYPE } },
    );
    vi.mocked(createServicePackage).mockRejectedValue(
      new Error("Package code is already in use."),
    );
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save package" }),
    );
    const failure = await within(dialog).findByRole("alert");
    expect(failure).toHaveTextContent("Package code is already in use.");
    expect(dialog).toContainElement(failure);
    expect(screen.getAllByRole("alert")).toEqual([failure]);
    expect(screen.queryByText("Package created.")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Create package" })).toBe(dialog);

    await waitFor(() =>
      expect(createServicePackage).toHaveBeenCalledWith({
        type: "CLASS",
        code: "PT10",
        name: "Ten sessions",
        session_count: 10,
        validity_value: 3,
        validity_unit: "MONTH",
        price: "100.00",
        class_type_id: CLASS_TYPE,
      }),
    );

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(
      screen.queryByRole("dialog", { name: "Create package" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Package code is already in use."),
    ).not.toBeInTheDocument();
  });

  it("ignores dismissals while saving and closes after the package is created", async () => {
    const pendingCreate = deferred<never>();
    vi.mocked(createServicePackage).mockImplementation(
      () => pendingCreate.promise as never,
    );
    renderManager();
    await screen.findByText("Ten sessions");
    const listCalls = vi.mocked(listServicePackages).mock.calls.length;
    const dialog = await openCreateDialog();
    fillPackage(dialog);
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save package" }),
    );

    await waitFor(() => expect(createServicePackage).toHaveBeenCalledTimes(1));
    expect(createServicePackage).toHaveBeenCalledWith({
      type: "PT",
      code: "PT10",
      name: "Ten sessions",
      session_count: 10,
      validity_value: 3,
      validity_unit: "MONTH",
      price: "100.00",
    });
    expect(createServicePackage).not.toHaveBeenCalledWith(
      expect.objectContaining({ class_type_id: expect.anything() }),
    );
    expect(dialog).toHaveAttribute("aria-busy", "true");
    expect(
      within(dialog).getByRole("button", { name: "Close" }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "Cancel" }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "Save package" }),
    ).toBeDisabled();
    expect(within(dialog).getByLabelText("Code")).toBeDisabled();

    fireEvent.keyDown(dialog, { key: "Escape" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    fireEvent.mouseDown(dialog.parentElement ?? dialog);
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save package" }),
    );
    expect(screen.getByRole("dialog", { name: "Create package" })).toBe(dialog);
    expect(createServicePackage).toHaveBeenCalledTimes(1);

    await act(async () => {
      pendingCreate.resolve({} as never);
      await pendingCreate.promise;
    });
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Create package" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByText("Package created.")).toBeInTheDocument();
    await waitFor(() =>
      expect(vi.mocked(listServicePackages).mock.calls.length).toBeGreaterThan(
        listCalls,
      ),
    );
  });
});
