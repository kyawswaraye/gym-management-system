import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  adjustStock,
  getProduct,
  listLowStock,
  listProductCategories,
  listProducts,
  listStockMovements,
} from "@/features/finance/client";
import { InventoryManager } from "@/features/finance/inventory-manager";
import type { Product, ProductCategory } from "@/types/api";

vi.mock("@/features/finance/client", () => ({
  adjustStock: vi.fn(),
  createProduct: vi.fn(),
  createProductCategory: vi.fn(),
  getProduct: vi.fn(),
  listLowStock: vi.fn(),
  listProductCategories: vi.fn(),
  listProducts: vi.fn(),
  listStockMovements: vi.fn(),
  updateProduct: vi.fn(),
}));

const PRODUCT_ID = "11111111-1111-4111-8111-111111111111";
const CATEGORY_ID = "22222222-2222-4222-8222-222222222222";

function category(): ProductCategory {
  return {
    id: CATEGORY_ID,
    code: "DRINKS",
    name: "Drinks",
    is_active: true,
    archived_at: null,
    created_by_membership_id: CATEGORY_ID,
    updated_by_membership_id: CATEGORY_ID,
    created_at: "2026-10-18T01:00:00Z",
    updated_at: "2026-10-18T01:00:00Z",
  };
}

function product(overrides: Partial<Product> = {}): Product {
  return {
    id: PRODUCT_ID,
    sku: "WATER",
    barcode: null,
    name: "Water",
    category_id: CATEGORY_ID,
    unit: "PIECE",
    sale_price: "1.00",
    current_unit_cost: "0.40",
    default_unit_cost: "0.40",
    low_stock_threshold: "5.000",
    quantity_on_hand: "8.000",
    sold_quantity: "0.000",
    revenue: "0.00",
    cost_of_goods_sold: "0.00",
    gross_profit: "0.00",
    is_active: true,
    archived_at: null,
    created_by_membership_id: CATEGORY_ID,
    updated_by_membership_id: CATEGORY_ID,
    created_at: "2026-10-18T01:00:00Z",
    updated_at: "2026-10-18T01:00:00Z",
    ...overrides,
  };
}

describe("inventory", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listProductCategories).mockResolvedValue({
      data: [],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(listLowStock).mockResolvedValue({
      data: [],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(listStockMovements).mockResolvedValue({
      data: [],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(getProduct).mockResolvedValue(product());
  });

  it("pages products and adjusts stock with an exact quantity", async () => {
    vi.mocked(listProducts)
      .mockResolvedValueOnce({
        data: [product()],
        meta: { has_more: true, next_after: "stock-2" },
      })
      .mockResolvedValueOnce({
        data: [product({ id: CATEGORY_ID, sku: "TOWEL" })],
        meta: { has_more: false, next_after: null },
      });
    vi.mocked(adjustStock).mockResolvedValue({
      movement: {
        id: "33333333-3333-4333-8333-333333333333",
        product_id: PRODUCT_ID,
        quantity: "1.250",
        movement_type: "PURCHASE",
        unit_cost_snapshot: "0.40",
        reason: "Delivery",
        business_date: "2026-10-18",
        created_by_membership_id: CATEGORY_ID,
        created_at: "2026-10-18T02:00:00Z",
      },
      product: product({ quantity_on_hand: "9.250" }),
    });
    render(
      <InventoryManager canAdjust canManage={false} defaultDate="2026-10-18" />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Next page" }));
    await screen.findByRole("button", { name: "TOWEL" });
    expect(
      screen.queryByRole("button", { name: "WATER" }),
    ).not.toBeInTheDocument();
    expect(vi.mocked(listProducts).mock.calls[1]?.[0]).toMatchObject({
      after: "stock-2",
    });
  });

  it("excludes sale movements from the adjustment control", async () => {
    vi.mocked(listProducts).mockResolvedValue({
      data: [product()],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(adjustStock).mockResolvedValue({
      movement: {
        id: "33333333-3333-4333-8333-333333333333",
        product_id: PRODUCT_ID,
        quantity: "-1.000",
        movement_type: "DAMAGE",
        unit_cost_snapshot: "0.40",
        reason: "Broken bottle",
        business_date: "2026-10-18",
        created_by_membership_id: CATEGORY_ID,
        created_at: "2026-10-18T02:00:00Z",
      },
      product: product(),
    });
    render(
      <InventoryManager canAdjust canManage={false} defaultDate="2026-10-18" />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "WATER" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Adjust stock" }),
    );
    const type = screen.getByRole("combobox", { name: "Movement type" });
    expect(type).not.toHaveTextContent("SALE_RETURN");
    expect(type).not.toHaveTextContent("SALE");
    fireEvent.change(screen.getByRole("textbox", { name: "Quantity" }), {
      target: { value: "-1" },
    });
    fireEvent.change(type, { target: { value: "DAMAGE" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Reason" }), {
      target: { value: "Broken bottle" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm adjustment" }));
    await waitFor(() => {
      expect(adjustStock).toHaveBeenCalledWith({
        product_id: PRODUCT_ID,
        quantity: "-1.000",
        movement_type: "DAMAGE",
        business_date: "2026-10-18",
        reason: "Broken bottle",
      });
    });
    expect(JSON.stringify(vi.mocked(adjustStock).mock.calls[0])).not.toContain(
      "quantity_on_hand",
    );
  });

  it("hides adjustment without permission", async () => {
    vi.mocked(listProducts).mockResolvedValue({
      data: [product()],
      meta: { has_more: false, next_after: null },
    });
    render(
      <InventoryManager
        canAdjust={false}
        canManage={false}
        defaultDate="2026-10-18"
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "WATER" }));
    expect(
      screen.queryByRole("button", { name: "Adjust stock" }),
    ).not.toBeInTheDocument();
  });

  it("keeps catalog, detail, and the other sections on separate surfaces", async () => {
    vi.mocked(listProductCategories).mockResolvedValue({
      data: [category()],
      meta: { has_more: false, next_after: null },
    });
    vi.mocked(listProducts).mockResolvedValue({
      data: [product()],
      meta: { has_more: false, next_after: null },
    });
    render(<InventoryManager canAdjust canManage defaultDate="2026-10-18" />);

    fireEvent.click(await screen.findByRole("button", { name: "New product" }));
    expect(
      screen.getByRole("heading", { name: "New product" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(
      screen.queryByRole("heading", { name: "New product" }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "WATER" }));
    const detail = await screen.findByRole("region", { name: "Water" });
    expect(within(detail).getByText("Quantity on hand")).toBeInTheDocument();
    expect(within(detail).getByText("Drinks")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Edit product" }));
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Water");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    fireEvent.click(screen.getByRole("tab", { name: "Categories" }));
    expect(
      screen.getByRole("heading", { name: "Product categories" }),
    ).toBeInTheDocument();
    expect(screen.getByText("DRINKS")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Stock movements" }));
    expect(
      screen.getByRole("heading", { name: "Stock movements" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Apply filters" }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Low stock" }));
    expect(
      screen.getByRole("heading", { name: "Low stock" }),
    ).toBeInTheDocument();
  });
});
