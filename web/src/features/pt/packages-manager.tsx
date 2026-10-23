"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import {
  Alert,
  CursorControls,
  Field,
  SelectInput,
  TextInput,
} from "@/features/finance/ui";
import { useCursorPages } from "@/features/finance/paging";
import { useLookupOptions } from "@/features/pt/lookup";
import {
  createServicePackage,
  listClassTypes,
  listServicePackages,
} from "@/features/pt/client";
import { phase5Error } from "@/features/pt/errors";
import type { ClassType, ServicePackage, ValidityUnit } from "@/types/api";

const PAGE = "25";
const PACKAGE_CODE = /^[A-Z][A-Z0-9_]{0,31}$/;
const PACKAGE_PRICE = /^\d+(\.\d{1,2})?$/;

const cardClass =
  "overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm";

function validityUnit(value: string): ValidityUnit {
  if (
    value === "DAY" ||
    value === "WEEK" ||
    value === "YEAR" ||
    value === "MONTH"
  ) {
    return value;
  }
  return "MONTH";
}

function validityLabel(value: number, unit: string): string {
  const words: Record<string, string> = {
    DAY: "day",
    WEEK: "week",
    MONTH: "month",
    YEAR: "year",
  };
  const word = words[unit];
  if (!word) return `${value} ${unit.toLowerCase()}`;
  return `${value} ${word}${value === 1 ? "" : "s"}`;
}

function packageFormError(input: {
  type: "PT" | "CLASS";
  code: string;
  name: string;
  sessionCount: number;
  validityValue: number;
  price: string;
  classTypeId: string;
  classTypesPending: boolean;
}): string {
  if (!PACKAGE_CODE.test(input.code)) {
    return "Enter an uppercase package code.";
  }
  if (input.name.trim() === "") return "Enter a package name.";
  if (!Number.isInteger(input.sessionCount) || input.sessionCount < 1) {
    return "Enter at least 1 session.";
  }
  if (!Number.isInteger(input.validityValue) || input.validityValue < 1) {
    return "Enter a validity of at least 1.";
  }
  if (!PACKAGE_PRICE.test(input.price)) {
    return "Enter a price with up to 2 decimal places.";
  }
  if (input.type === "CLASS" && input.classTypesPending) {
    return "Class types are still loading.";
  }
  if (input.type === "CLASS" && input.classTypeId === "") {
    return "A class package requires a class type.";
  }
  return "";
}

export function PackagesManager({
  canManage,
  currency,
}: {
  canManage: boolean;
  currency: string;
}) {
  const [type, setType] = useState("");
  const [activeOnly, setActiveOnly] = useState("");
  const [notice, setNotice] = useState("");
  const [formError, setFormError] = useState("");
  const [pending, setPending] = useState(false);
  const [open, setOpen] = useState(false);
  const [packageType, setPackageType] = useState<"PT" | "CLASS">("PT");
  const pendingRef = useRef(false);
  const dialogRef = useRef<HTMLFormElement>(null);
  const createButtonRef = useRef<HTMLButtonElement>(null);
  const dialogWasOpen = useRef(false);
  const packages = useCursorPages<ServicePackage>(
    `packages:${type}:${activeOnly}`,
    (after) =>
      listServicePackages({
        limit: PAGE,
        ...(after ? { after } : {}),
        ...(type ? { type } : {}),
        ...(activeOnly ? { is_active: activeOnly } : {}),
      }),
  );
  const classTypes = useLookupOptions<ClassType>(
    "class-types-for-packages",
    (after) =>
      listClassTypes({
        limit: "100",
        is_active: "true",
        ...(after ? { after } : {}),
      }),
    open && packageType === "CLASS",
  );
  const filtered = type !== "" || activeOnly !== "";
  const showPaging = packages.index > 0 || packages.hasMore || packages.bounded;
  const currencyLabel = currency || "the gym currency";

  useEffect(() => {
    if (open) {
      dialogWasOpen.current = true;
      dialogRef.current?.focus({ preventScroll: true });
      return;
    }
    if (!dialogWasOpen.current) return;
    dialogWasOpen.current = false;
    createButtonRef.current?.focus();
  }, [open]);

  function openCreate() {
    if (pendingRef.current || open) return;
    setPackageType("PT");
    setFormError("");
    setOpen(true);
  }

  function closeCreate() {
    if (pendingRef.current) return;
    setFormError("");
    setOpen(false);
  }

  function submitPackage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pendingRef.current) return;
    const form = new FormData(event.currentTarget);
    const classTypeId = String(form.get("class_type_id") ?? "");
    const code = String(form.get("code") ?? "");
    const name = String(form.get("name") ?? "");
    const sessionCount = Number(form.get("session_count"));
    const validityValue = Number(form.get("validity_value"));
    const price = String(form.get("price") ?? "");
    const problem = packageFormError({
      type: packageType,
      code,
      name,
      sessionCount,
      validityValue,
      price,
      classTypeId,
      classTypesPending: classTypes.pending,
    });
    if (problem) {
      setFormError(problem);
      return;
    }
    pendingRef.current = true;
    setPending(true);
    setFormError("");
    setNotice("");
    void createServicePackage({
      type: packageType,
      code,
      name,
      session_count: sessionCount,
      validity_value: validityValue,
      validity_unit: validityUnit(String(form.get("validity_unit") ?? "")),
      price,
      ...(packageType === "CLASS" ? { class_type_id: classTypeId } : {}),
    })
      .then(() => {
        setNotice("Package created.");
        setFormError("");
        setOpen(false);
        packages.reload();
      })
      .catch((caught: unknown) => {
        setFormError(phase5Error(caught, "The package could not be created."));
      })
      .finally(() => {
        pendingRef.current = false;
        setPending(false);
      });
  }

  return (
    <div className="grid gap-6">
      <div inert={open}>
        {notice ? (
          <p
            aria-live="polite"
            className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-800"
            role="status"
          >
            {notice}
          </p>
        ) : null}

        <section
          aria-busy={packages.pending}
          aria-labelledby="package-catalog-title"
          className={cardClass}
        >
          <div className="flex flex-col gap-3 border-b border-slate-200 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
            <div>
              <h2
                className="text-sm font-bold tracking-wide text-slate-700 uppercase"
                id="package-catalog-title"
              >
                Service packages
              </h2>
              <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-500">
                Session count, validity, and price. Saved definitions stay
                unchanged.
              </p>
            </div>
            {canManage ? (
              <button
                aria-controls={open ? "create-package-dialog" : undefined}
                aria-expanded={open}
                className="button button-create w-full sm:w-auto"
                onClick={openCreate}
                ref={createButtonRef}
                type="button"
              >
                <span aria-hidden="true">+</span>
                Create package
              </button>
            ) : null}
          </div>

          <div className="border-b border-slate-200 bg-slate-50/70 px-4 py-3 sm:px-5">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
              <label className="grid gap-1 text-xs font-semibold tracking-wide text-slate-500 uppercase sm:w-40">
                Type
                <SelectInput
                  aria-label="Filter by package type"
                  onChange={(event) => setType(event.target.value)}
                  value={type}
                >
                  <option value="">All types</option>
                  <option value="PT">PT</option>
                  <option value="CLASS">CLASS</option>
                </SelectInput>
              </label>
              <label className="grid gap-1 text-xs font-semibold tracking-wide text-slate-500 uppercase sm:w-40">
                Status
                <SelectInput
                  aria-label="Filter by status"
                  onChange={(event) => setActiveOnly(event.target.value)}
                  value={activeOnly}
                >
                  <option value="">Any</option>
                  <option value="true">Active</option>
                  <option value="false">Inactive</option>
                </SelectInput>
              </label>
              {filtered ? (
                <button
                  className="text-sm font-semibold text-slate-600 underline-offset-4 hover:text-slate-900 hover:underline sm:mb-2"
                  onClick={() => {
                    setType("");
                    setActiveOnly("");
                  }}
                  type="button"
                >
                  Clear filters
                </button>
              ) : null}
            </div>
          </div>

          <div className="grid gap-4 p-4 sm:p-5">
            {packages.pending && packages.rows.length === 0 ? (
              <div className="grid gap-3" role="status">
                <p className="text-sm font-medium text-slate-600">
                  Loading packages.
                </p>
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  {[0, 1, 2].map((item) => (
                    <div
                      className="h-36 animate-pulse rounded-xl bg-slate-100"
                      key={item}
                    />
                  ))}
                </div>
              </div>
            ) : null}
            {packages.pending && packages.rows.length > 0 ? (
              <p className="text-sm font-medium text-slate-600" role="status">
                Loading packages.
              </p>
            ) : null}
            {!packages.pending && packages.error ? (
              <div className="grid gap-3 rounded-xl border border-red-200 bg-red-50 p-4">
                <p className="text-sm font-medium text-red-800" role="alert">
                  {packages.error}
                </p>
                <button
                  className="button w-fit"
                  onClick={() => packages.reload()}
                  type="button"
                >
                  Retry loading packages
                </button>
              </div>
            ) : null}
            {!packages.pending &&
            !packages.error &&
            packages.rows.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-6 py-12 text-center">
                <p className="font-semibold text-slate-700">
                  No packages match.
                </p>
                <p className="mt-1 text-sm text-slate-500">
                  {filtered
                    ? "Try changing or clearing the current filters."
                    : canManage
                      ? "Create a package to add it to the catalog."
                      : "Packages will appear here when they are added."}
                </p>
              </div>
            ) : null}
            {packages.rows.length > 0 ? (
              <ul
                aria-label="Package catalog"
                className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3"
              >
                {packages.rows.map((row) => {
                  const classPackage = row.type === "CLASS";
                  return (
                    <li
                      className={`flex h-full flex-col rounded-xl border border-slate-200 border-l-4 p-4 ${
                        classPackage ? "border-l-sky-500" : "border-l-amber-400"
                      } ${row.is_active ? "bg-white" : "bg-slate-50"}`}
                      key={row.id}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <h3 className="text-base font-bold break-words text-slate-900">
                            {row.name}
                          </h3>
                          <p className="mt-1 text-xs font-semibold tracking-wide text-slate-500">
                            {row.code}
                          </p>
                        </div>
                        <div className="flex shrink-0 flex-col items-end gap-1.5">
                          <span
                            className={`rounded-full px-2.5 py-1 text-xs font-bold ${
                              classPackage
                                ? "bg-sky-100 text-sky-800"
                                : "bg-amber-100 text-amber-800"
                            }`}
                          >
                            {row.type}
                          </span>
                          <span
                            className={`rounded-full px-2.5 py-1 text-xs font-bold ${
                              row.is_active
                                ? "bg-emerald-100 text-emerald-700"
                                : "bg-slate-200 text-slate-600"
                            }`}
                          >
                            {row.is_active ? "Active" : "Inactive"}
                          </span>
                        </div>
                      </div>
                      <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-slate-100 pt-3 sm:grid-cols-3">
                        <div>
                          <dt className="text-xs text-slate-500">Sessions</dt>
                          <dd className="mt-0.5 font-semibold text-slate-900 tabular-nums">
                            {row.session_count}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-xs text-slate-500">Validity</dt>
                          <dd className="mt-0.5 font-semibold text-slate-900">
                            {validityLabel(
                              row.validity_value,
                              row.validity_unit,
                            )}
                          </dd>
                        </div>
                        <div className="col-span-2 sm:col-span-1">
                          <dt className="text-xs text-slate-500">Price</dt>
                          <dd className="mt-0.5 font-semibold break-words text-slate-900 tabular-nums">
                            {row.price}{" "}
                            <span className="text-xs font-medium text-slate-500">
                              {row.currency}
                            </span>
                          </dd>
                        </div>
                      </dl>
                    </li>
                  );
                })}
              </ul>
            ) : null}
          </div>
          {showPaging ? (
            <div className="border-t border-slate-100 px-4 py-4 sm:px-5 [&_nav]:border-t-0 [&_nav]:pt-0">
              <CursorControls
                bounded={packages.bounded}
                hasMore={packages.hasMore}
                index={packages.index}
                onNext={() => void packages.goNext()}
                onPrevious={() => void packages.goPrevious()}
                pending={packages.pending}
              />
            </div>
          ) : null}
        </section>
      </div>

      {open && canManage ? (
        <div
          className="fixed inset-0 z-50 grid overflow-hidden p-2 sm:place-items-center sm:p-6"
          onMouseDown={(event) => {
            if (event.target !== event.currentTarget) return;
            closeCreate();
          }}
          role="presentation"
        >
          <form
            aria-busy={pending}
            aria-describedby="create-package-description"
            aria-labelledby="create-package-title"
            aria-modal="true"
            className="flex h-full max-h-full min-h-0 w-full flex-col overflow-y-auto overscroll-contain sm:h-auto sm:max-h-[min(90dvh,48rem)] sm:max-w-2xl"
            id="create-package-dialog"
            noValidate
            onKeyDown={(event) => {
              if (event.key !== "Escape") return;
              event.stopPropagation();
              if (pendingRef.current) return;
              event.preventDefault();
              closeCreate();
            }}
            onSubmit={submitPackage}
            ref={dialogRef}
            role="dialog"
            tabIndex={-1}
          >
            <div className="sticky top-0 z-10 flex items-start gap-3 border-b border-slate-100 bg-white px-5 py-4 sm:px-6">
              <span
                aria-hidden="true"
                className={`grid min-h-9 shrink-0 place-items-center rounded-lg px-2 text-[0.65rem] font-black tracking-wide ${
                  packageType === "CLASS"
                    ? "bg-sky-100 text-sky-800"
                    : "bg-amber-100 text-amber-900"
                }`}
              >
                {packageType}
              </span>
              <div className="min-w-0 flex-1">
                <h2
                  className="text-xl font-bold text-slate-900"
                  id="create-package-title"
                >
                  Create package
                </h2>
                <p
                  className="mt-1 text-sm text-slate-500"
                  id="create-package-description"
                >
                  This definition stays unchanged after it is saved. Price is
                  charged in {currencyLabel}.
                </p>
              </div>
              <button
                className="button button-cancel button-sm"
                disabled={pending}
                onClick={closeCreate}
                type="button"
              >
                Close
              </button>
            </div>
            <fieldset
              className="m-0 grid w-full min-w-0 gap-5 border-0 px-5 py-4 sm:px-6"
              disabled={pending}
            >
              <div className="grid gap-3">
                <div>
                  <h3 className="text-sm font-bold text-slate-800">Identity</h3>
                  <p className="mt-1 text-xs leading-5 text-slate-500">
                    Use an uppercase code such as PT10. The name is what staff
                    see.
                  </p>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Type">
                    <SelectInput
                      name="type"
                      onChange={(event) => {
                        setPackageType(
                          event.target.value === "CLASS" ? "CLASS" : "PT",
                        );
                        setFormError("");
                      }}
                      value={packageType}
                    >
                      <option value="PT">PT</option>
                      <option value="CLASS">CLASS</option>
                    </SelectInput>
                  </Field>
                  <Field label="Code">
                    <TextInput
                      maxLength={32}
                      name="code"
                      placeholder="PT10"
                      required
                    />
                  </Field>
                  <div className="sm:col-span-2">
                    <Field label="Name">
                      <TextInput
                        maxLength={120}
                        name="name"
                        placeholder="Ten PT sessions"
                        required
                      />
                    </Field>
                  </div>
                </div>
              </div>

              <div className="grid gap-3 border-t border-slate-100 pt-5">
                <div>
                  <h3 className="text-sm font-bold text-slate-800">
                    Sessions and validity
                  </h3>
                  <p className="mt-1 text-xs leading-5 text-slate-500">
                    Both values must be at least 1. Validity starts when the
                    package is granted.
                  </p>
                </div>
                <div className="grid gap-4 sm:grid-cols-3">
                  <Field label="Sessions">
                    <TextInput
                      min={1}
                      name="session_count"
                      required
                      step={1}
                      type="number"
                    />
                  </Field>
                  <Field label="Validity">
                    <TextInput
                      min={1}
                      name="validity_value"
                      required
                      step={1}
                      type="number"
                    />
                  </Field>
                  <Field label="Validity unit">
                    <SelectInput defaultValue="MONTH" name="validity_unit">
                      <option value="DAY">DAY</option>
                      <option value="WEEK">WEEK</option>
                      <option value="MONTH">MONTH</option>
                      <option value="YEAR">YEAR</option>
                    </SelectInput>
                  </Field>
                </div>
              </div>

              <div className="grid gap-3 border-t border-slate-100 pt-5">
                <div>
                  <h3 className="text-sm font-bold text-slate-800">Price</h3>
                  <p className="mt-1 text-xs leading-5 text-slate-500">
                    Charged in {currencyLabel}. Use up to 2 decimal places.
                  </p>
                </div>
                <Field label="Price">
                  <TextInput
                    inputMode="decimal"
                    name="price"
                    placeholder="0.00"
                    required
                  />
                </Field>
              </div>

              {packageType === "CLASS" ? (
                <div className="grid gap-3 border-t border-slate-100 pt-5">
                  <div>
                    <h3 className="text-sm font-bold text-slate-800">Class</h3>
                    <p className="mt-1 text-xs leading-5 text-slate-500">
                      A class package requires an active class type.
                    </p>
                  </div>
                  {classTypes.pending ? (
                    <p className="text-sm text-slate-500" role="status">
                      Loading class types.
                    </p>
                  ) : null}
                  <Alert message={classTypes.error} />
                  {!classTypes.pending &&
                  !classTypes.error &&
                  classTypes.items.length === 0 ? (
                    <p className="text-sm text-slate-500">
                      No active class types are available.
                    </p>
                  ) : null}
                  <Field label="Class type for a CLASS package">
                    <SelectInput name="class_type_id" required>
                      <option value="">Select a class type</option>
                      {classTypes.items.map((row) => (
                        <option key={row.id} value={row.id}>
                          {row.code} · {row.name}
                        </option>
                      ))}
                    </SelectInput>
                  </Field>
                </div>
              ) : null}
            </fieldset>
            <div className="sticky bottom-0 z-10 mt-auto border-t border-slate-100 bg-white px-5 py-4 sm:px-6">
              {formError ? (
                <div className="mb-3">
                  <Alert message={formError} />
                </div>
              ) : null}
              <div className="flex flex-col sm:flex-row">
                <button
                  className="button button-cancel w-full sm:w-auto"
                  disabled={pending}
                  onClick={closeCreate}
                  type="button"
                >
                  Cancel
                </button>
                <button
                  className="button button-create w-full sm:w-auto"
                  disabled={pending}
                  type="submit"
                >
                  Save package
                </button>
              </div>
            </div>
          </form>
        </div>
      ) : null}
    </div>
  );
}
