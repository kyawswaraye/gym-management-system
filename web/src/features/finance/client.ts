import {
  OperationError,
  operationGet,
  operationList,
  operationMutation,
} from "@/features/operations/client";
import type { CollectionPage } from "@/features/operations/client";
import type {
  AdjustCashSessionRequest,
  ApproveExpenseRequest,
  ApprovePaymentRequest,
  ApproveRetailSaleRequest,
  ApproveSalaryRequest,
  CancelPaymentRequest,
  CashCommandData,
  CashSession,
  CashTransaction,
  Expense,
  ExpenseApproval,
  ExpenseCategory,
  ExpenseCategoryUpdateRequest,
  ExpenseCategoryWriteRequest,
  ExpenseUpdateRequest,
  ExpenseVoid,
  ExpenseWriteRequest,
  OpenCashSessionRequest,
  Payment,
  PaymentApproval,
  PaymentCorrection,
  PaymentUpdateRequest,
  PaymentWriteRequest,
  Product,
  ProductCategory,
  ProductCategoryWriteRequest,
  ProductUpdateRequest,
  ProductWriteRequest,
  Receipt,
  ReconcileCashSessionRequest,
  RefundPaymentRequest,
  RejectPaymentRequest,
  RetailSale,
  RetailSaleApproval,
  RetailSaleUpdateRequest,
  RetailSaleReturn,
  RetailSaleVoid,
  RetailSaleWriteRequest,
  ReturnRetailSaleRequest,
  SalaryApproval,
  SalaryLinesReplaceRequest,
  SalaryPeriod,
  SalaryPeriodUpdateRequest,
  SalaryPeriodWriteRequest,
  SalaryVoid,
  StockAdjustment,
  StockAdjustRequest,
  StockMovement,
  VoidExpenseRequest,
  VoidPaymentRequest,
  VoidReceiptRequest,
  VoidRetailSaleRequest,
  VoidSalaryRequest,
} from "@/types/api";

export type { CollectionPage };

const RESOURCE_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function commandKey(): string {
  return crypto.randomUUID();
}

function resourceId(value: string, label: string): string {
  const id = value.toLowerCase();
  if (!RESOURCE_ID.test(id))
    throw new OperationError(`${label} was not found.`);
  return id;
}

function query(values: Record<string, string | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value) params.set(key, value);
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

function listed<T>(path: string, values: Record<string, string | undefined>) {
  return operationList<T>(`${path}${query(values)}`);
}

function command<T>(
  path: string,
  body: object,
  method: "POST" | "PATCH" | "PUT" = "POST",
): Promise<T> {
  return operationMutation<T>(path, body, method, commandKey());
}

export function listCashSessions(values: {
  status?: string;
  register_code?: string;
  business_date?: string;
  limit?: string;
  after?: string;
}): Promise<CollectionPage<CashSession>> {
  return listed<CashSession>("cash-sessions/", values);
}

export function getCashSession(sessionId: string): Promise<CashSession> {
  return operationGet<CashSession>(
    `cash-sessions/${resourceId(sessionId, "Cash session")}/`,
  );
}

export function openCashSession(
  body: OpenCashSessionRequest,
): Promise<CashCommandData> {
  return command<CashCommandData>("cash-sessions/open/", body);
}

export function adjustCashSession(
  sessionId: string,
  body: AdjustCashSessionRequest,
): Promise<CashCommandData> {
  return command<CashCommandData>(
    `cash-sessions/${resourceId(sessionId, "Cash session")}/adjustments/`,
    body,
  );
}

export function reconcileCashSession(
  sessionId: string,
  body: ReconcileCashSessionRequest,
): Promise<CashCommandData> {
  return command<CashCommandData>(
    `cash-sessions/${resourceId(sessionId, "Cash session")}/reconcile/`,
    body,
  );
}

export function listCashTransactions(values: {
  cash_session_id?: string;
  business_date?: string;
  source_type?: string;
  direction?: string;
  limit?: string;
  after?: string;
}): Promise<CollectionPage<CashTransaction>> {
  return listed<CashTransaction>("cash-transactions/", values);
}

export function listPayments(values: {
  status?: string;
  business_date?: string;
  member?: string;
  membership?: string;
  method?: string;
  purpose?: string;
  limit?: string;
  after?: string;
}): Promise<CollectionPage<Payment>> {
  return listed<Payment>("payments/", values);
}

export function getPayment(paymentId: string): Promise<Payment> {
  return operationGet<Payment>(`payments/${resourceId(paymentId, "Payment")}/`);
}

export function createPayment(body: PaymentWriteRequest): Promise<Payment> {
  return command<Payment>("payments/", body);
}

export function updatePayment(
  paymentId: string,
  body: PaymentUpdateRequest,
): Promise<Payment> {
  return command<Payment>(
    `payments/${resourceId(paymentId, "Payment")}/`,
    body,
    "PATCH",
  );
}

export function submitPayment(paymentId: string): Promise<Payment> {
  return command<Payment>(
    `payments/${resourceId(paymentId, "Payment")}/submit/`,
    {},
  );
}

export function approvePayment(
  paymentId: string,
  body: ApprovePaymentRequest,
): Promise<PaymentApproval> {
  return command<PaymentApproval>(
    `payments/${resourceId(paymentId, "Payment")}/approve/`,
    body,
  );
}

export function rejectPayment(
  paymentId: string,
  body: RejectPaymentRequest,
): Promise<Payment> {
  return command<Payment>(
    `payments/${resourceId(paymentId, "Payment")}/reject/`,
    body,
  );
}

export function cancelPayment(
  paymentId: string,
  body: CancelPaymentRequest,
): Promise<Payment> {
  return command<Payment>(
    `payments/${resourceId(paymentId, "Payment")}/cancel/`,
    body,
  );
}

export function voidPayment(
  paymentId: string,
  body: VoidPaymentRequest,
): Promise<PaymentCorrection> {
  return command<PaymentCorrection>(
    `payments/${resourceId(paymentId, "Payment")}/void/`,
    body,
  );
}

export function refundPayment(
  paymentId: string,
  body: RefundPaymentRequest,
): Promise<PaymentCorrection> {
  return command<PaymentCorrection>(
    `payments/${resourceId(paymentId, "Payment")}/refund/`,
    body,
  );
}

export function listReceipts(values: {
  status?: string;
  business_date?: string;
  member?: string;
  payment?: string;
  limit?: string;
  after?: string;
}): Promise<CollectionPage<Receipt>> {
  return listed<Receipt>("receipts/", values);
}

export function getReceipt(receiptId: string): Promise<Receipt> {
  return operationGet<Receipt>(`receipts/${resourceId(receiptId, "Receipt")}/`);
}

export function voidReceipt(
  receiptId: string,
  body: VoidReceiptRequest,
): Promise<Receipt> {
  return command<Receipt>(
    `receipts/${resourceId(receiptId, "Receipt")}/void/`,
    body,
  );
}

export async function downloadReceiptDocument(
  receiptId: string,
): Promise<string> {
  const id = resourceId(receiptId, "Receipt");
  const { getCsrfToken } = await import("@/features/auth/client");
  const csrf = await getCsrfToken();
  const response = await fetch(`/api/operations/receipts/${id}/download/`, {
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "text/html", "X-CSRF-Token": csrf },
  });
  const type = response.headers.get("content-type") ?? "";
  if (!response.ok || !type.includes("text/html")) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    throw readDownloadError(body);
  }
  return response.text();
}

export function listExpenseCategories(values: {
  is_active?: string;
  limit?: string;
  after?: string;
}): Promise<CollectionPage<ExpenseCategory>> {
  return listed<ExpenseCategory>("expense-categories/", values);
}

export function createExpenseCategory(
  body: ExpenseCategoryWriteRequest,
): Promise<ExpenseCategory> {
  return command<ExpenseCategory>("expense-categories/", body);
}

export function updateExpenseCategory(
  categoryId: string,
  body: ExpenseCategoryUpdateRequest,
): Promise<ExpenseCategory> {
  return command<ExpenseCategory>(
    `expense-categories/${resourceId(categoryId, "Category")}/`,
    body,
    "PATCH",
  );
}

export function listExpenses(values: {
  status?: string;
  business_date?: string;
  category?: string;
  payment_method?: string;
  limit?: string;
  after?: string;
}): Promise<CollectionPage<Expense>> {
  return listed<Expense>("expenses/", values);
}

export function getExpense(expenseId: string): Promise<Expense> {
  return operationGet<Expense>(`expenses/${resourceId(expenseId, "Expense")}/`);
}

export function createExpense(body: ExpenseWriteRequest): Promise<Expense> {
  return command<Expense>("expenses/", body);
}

export function updateExpense(
  expenseId: string,
  body: ExpenseUpdateRequest,
): Promise<Expense> {
  return command<Expense>(
    `expenses/${resourceId(expenseId, "Expense")}/`,
    body,
    "PATCH",
  );
}

export function submitExpense(expenseId: string): Promise<Expense> {
  return command<Expense>(
    `expenses/${resourceId(expenseId, "Expense")}/submit/`,
    {},
  );
}

export function approveExpense(
  expenseId: string,
  body: ApproveExpenseRequest,
): Promise<ExpenseApproval> {
  return command<ExpenseApproval>(
    `expenses/${resourceId(expenseId, "Expense")}/approve/`,
    body,
  );
}

export function rejectExpense(
  expenseId: string,
  body: { rejection_reason: string },
): Promise<Expense> {
  return command<Expense>(
    `expenses/${resourceId(expenseId, "Expense")}/reject/`,
    body,
  );
}

export function cancelExpense(
  expenseId: string,
  body: { reason: string },
): Promise<Expense> {
  return command<Expense>(
    `expenses/${resourceId(expenseId, "Expense")}/cancel/`,
    body,
  );
}

export function voidExpense(
  expenseId: string,
  body: VoidExpenseRequest,
): Promise<ExpenseVoid> {
  return command<ExpenseVoid>(
    `expenses/${resourceId(expenseId, "Expense")}/void/`,
    body,
  );
}

export function listSalaryPeriods(values: {
  status?: string;
  business_date?: string;
  limit?: string;
  after?: string;
}): Promise<CollectionPage<SalaryPeriod>> {
  return listed<SalaryPeriod>("salary-periods/", values);
}

export function getSalaryPeriod(periodId: string): Promise<SalaryPeriod> {
  return operationGet<SalaryPeriod>(
    `salary-periods/${resourceId(periodId, "Salary period")}/`,
  );
}

export function createSalaryPeriod(
  body: SalaryPeriodWriteRequest,
): Promise<SalaryPeriod> {
  return command<SalaryPeriod>("salary-periods/", body);
}

export function updateSalaryPeriod(
  periodId: string,
  body: SalaryPeriodUpdateRequest,
): Promise<SalaryPeriod> {
  return command<SalaryPeriod>(
    `salary-periods/${resourceId(periodId, "Salary period")}/`,
    body,
    "PATCH",
  );
}

export function replaceSalaryLines(
  periodId: string,
  body: SalaryLinesReplaceRequest,
): Promise<SalaryPeriod> {
  return command<SalaryPeriod>(
    `salary-periods/${resourceId(periodId, "Salary period")}/lines/`,
    body,
    "PUT",
  );
}

export function submitSalaryPeriod(periodId: string): Promise<SalaryPeriod> {
  return command<SalaryPeriod>(
    `salary-periods/${resourceId(periodId, "Salary period")}/submit/`,
    {},
  );
}

export function approveSalaryPeriod(
  periodId: string,
  body: ApproveSalaryRequest,
): Promise<SalaryApproval> {
  return command<SalaryApproval>(
    `salary-periods/${resourceId(periodId, "Salary period")}/approve/`,
    body,
  );
}

export function rejectSalaryPeriod(
  periodId: string,
  body: { rejection_reason: string },
): Promise<SalaryPeriod> {
  return command<SalaryPeriod>(
    `salary-periods/${resourceId(periodId, "Salary period")}/reject/`,
    body,
  );
}

export function cancelSalaryPeriod(
  periodId: string,
  body: { reason: string },
): Promise<SalaryPeriod> {
  return command<SalaryPeriod>(
    `salary-periods/${resourceId(periodId, "Salary period")}/cancel/`,
    body,
  );
}

export function voidSalaryPeriod(
  periodId: string,
  body: VoidSalaryRequest,
): Promise<SalaryVoid> {
  return command<SalaryVoid>(
    `salary-periods/${resourceId(periodId, "Salary period")}/void/`,
    body,
  );
}

export function listProductCategories(values: {
  is_active?: string;
  limit?: string;
  after?: string;
}): Promise<CollectionPage<ProductCategory>> {
  return listed<ProductCategory>("product-categories/", values);
}

export function createProductCategory(
  body: ProductCategoryWriteRequest,
): Promise<ProductCategory> {
  return command<ProductCategory>("product-categories/", body);
}

export function listProducts(values: {
  category_id?: string;
  is_active?: string;
  sku?: string;
  barcode?: string;
  limit?: string;
  after?: string;
}): Promise<CollectionPage<Product>> {
  return listed<Product>("products/", values);
}

export function listLowStock(values: {
  category_id?: string;
  limit?: string;
  after?: string;
}): Promise<CollectionPage<Product>> {
  return listed<Product>("products/low-stock/", values);
}

export function getProduct(productId: string): Promise<Product> {
  return operationGet<Product>(`products/${resourceId(productId, "Product")}/`);
}

export function createProduct(body: ProductWriteRequest): Promise<Product> {
  return command<Product>("products/", body);
}

export function updateProduct(
  productId: string,
  body: ProductUpdateRequest,
): Promise<Product> {
  return command<Product>(
    `products/${resourceId(productId, "Product")}/`,
    body,
    "PATCH",
  );
}

export function listStockMovements(values: {
  product_id?: string;
  movement_type?: string;
  business_date?: string;
  limit?: string;
  after?: string;
}): Promise<CollectionPage<StockMovement>> {
  return listed<StockMovement>("stock-movements/", values);
}

export function adjustStock(
  body: StockAdjustRequest,
): Promise<StockAdjustment> {
  return command<StockAdjustment>("stock-movements/adjust/", body);
}

export function listRetailSales(values: {
  status?: string;
  business_date?: string;
  member?: string;
  limit?: string;
  after?: string;
}): Promise<CollectionPage<RetailSale>> {
  return listed<RetailSale>("retail-sales/", values);
}

export function getRetailSale(saleId: string): Promise<RetailSale> {
  return operationGet<RetailSale>(
    `retail-sales/${resourceId(saleId, "Retail sale")}/`,
  );
}

export function createRetailSale(
  body: RetailSaleWriteRequest,
): Promise<RetailSale> {
  return command<RetailSale>("retail-sales/", body);
}

export function updateRetailSale(
  saleId: string,
  body: RetailSaleUpdateRequest,
): Promise<RetailSale> {
  return command<RetailSale>(
    `retail-sales/${resourceId(saleId, "Retail sale")}/`,
    body,
    "PATCH",
  );
}

export function submitRetailSale(saleId: string): Promise<RetailSale> {
  return command<RetailSale>(
    `retail-sales/${resourceId(saleId, "Retail sale")}/submit/`,
    {},
  );
}

export function approveRetailSale(
  saleId: string,
  body: ApproveRetailSaleRequest,
): Promise<RetailSaleApproval> {
  return command<RetailSaleApproval>(
    `retail-sales/${resourceId(saleId, "Retail sale")}/approve/`,
    body,
  );
}

export function rejectRetailSale(
  saleId: string,
  body: { rejection_reason: string },
): Promise<RetailSale> {
  return command<RetailSale>(
    `retail-sales/${resourceId(saleId, "Retail sale")}/reject/`,
    body,
  );
}

export function cancelRetailSale(
  saleId: string,
  body: { reason: string },
): Promise<RetailSale> {
  return command<RetailSale>(
    `retail-sales/${resourceId(saleId, "Retail sale")}/cancel/`,
    body,
  );
}

export function voidRetailSale(
  saleId: string,
  body: VoidRetailSaleRequest,
): Promise<RetailSaleVoid> {
  return command<RetailSaleVoid>(
    `retail-sales/${resourceId(saleId, "Retail sale")}/void/`,
    body,
  );
}

export function returnRetailSale(
  saleId: string,
  body: ReturnRetailSaleRequest,
): Promise<RetailSaleReturn> {
  return command<RetailSaleReturn>(
    `retail-sales/${resourceId(saleId, "Retail sale")}/return/`,
    body,
  );
}

function readDownloadError(body: unknown): OperationError {
  if (typeof body === "object" && body !== null && "error" in body) {
    const error = (body as { error?: { code?: unknown; message?: unknown } })
      .error;
    const code = typeof error?.code === "string" ? error.code : "";
    const message =
      typeof error?.message === "string" && error.message
        ? error.message
        : "The receipt could not be downloaded.";
    return new OperationError(message, {}, code);
  }
  return new OperationError("The receipt could not be downloaded.");
}
