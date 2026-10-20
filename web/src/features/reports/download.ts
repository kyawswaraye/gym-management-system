import { OperationError } from "@/features/operations/client";

const CSV_FILENAME =
  /^(?:report\.csv|(?:daily|monthly|cash-book|profit-loss|product-sales|membership-expiry|check-ins|pt-sessions|classes|stock-movements|inventory)-[A-Z0-9][A-Z0-9-]{1,31}(?:-\d{4}-\d{2}-\d{2}-\d{4}-\d{2}-\d{2})?\.csv)$/;

function csvFileName(header: string | null): string {
  const match = /^attachment;\s*filename="([^"]+)"$/.exec(header ?? "");
  const name = match?.[1] ?? "";
  return CSV_FILENAME.test(name) ? name : "report.csv";
}

function readDownloadError(body: unknown): OperationError {
  if (typeof body === "object" && body !== null && "error" in body) {
    const error = (body as { error?: { code?: unknown; message?: unknown } })
      .error;
    const code = typeof error?.code === "string" ? error.code : "";
    const message =
      typeof error?.message === "string" && error.message
        ? error.message
        : "The report could not be exported.";
    return new OperationError(message, {}, code);
  }
  return new OperationError("The report could not be exported.");
}

export async function downloadReportCsv(path: string): Promise<void> {
  if (
    !path.startsWith("/api/operations/reports/") ||
    path.includes("://") ||
    path.includes("..")
  ) {
    throw new OperationError("The report could not be exported.");
  }
  const { getCsrfToken } = await import("@/features/auth/client");
  const csrf = await getCsrfToken();
  const response = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "text/csv", "X-CSRF-Token": csrf },
  });
  const type = response.headers.get("content-type") ?? "";
  if (!response.ok || !type.includes("text/csv")) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    throw readDownloadError(body);
  }
  const blob = await response.blob();
  const filename = csvFileName(response.headers.get("content-disposition"));
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noopener";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
