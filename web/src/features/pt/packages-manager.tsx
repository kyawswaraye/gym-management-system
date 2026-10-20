"use client";

import { useState } from "react";

import {
  Alert,
  CursorControls,
  Empty,
  Field,
  Loading,
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

const PAGE = "25";

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
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [open, setOpen] = useState(false);
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
    open,
  );

  return (
    <div className="grid gap-4">
      <Alert message={error || packages.error} />
      {notice ? (
        <p aria-live="polite" role="status">
          {notice}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-3">
        <Field label="Package type">
          <SelectInput
            onChange={(event) => setType(event.target.value)}
            value={type}
          >
            <option value="">All types</option>
            <option value="PT">PT</option>
            <option value="CLASS">CLASS</option>
          </SelectInput>
        </Field>
        <Field label="Active">
          <SelectInput
            onChange={(event) => setActiveOnly(event.target.value)}
            value={activeOnly}
          >
            <option value="">Any</option>
            <option value="true">Active</option>
            <option value="false">Inactive</option>
          </SelectInput>
        </Field>
      </div>
      {packages.pending ? <Loading label="Loading packages." /> : null}
      {!packages.pending && packages.rows.length === 0 ? (
        <Empty label="No packages match." />
      ) : (
        <ul className="grid gap-3">
          {packages.rows.map((row) => (
            <li className="rounded border border-slate-200 p-3" key={row.id}>
              <p>
                {row.code} · {row.name} · {row.type}
                {row.is_active ? "" : " · inactive"}
              </p>
              <p className="muted">
                {row.session_count} sessions · {row.validity_value}{" "}
                {row.validity_unit} · {row.price} {row.currency}
              </p>
            </li>
          ))}
        </ul>
      )}
      <CursorControls
        bounded={packages.bounded}
        hasMore={packages.hasMore}
        index={packages.index}
        onNext={() => void packages.goNext()}
        onPrevious={() => void packages.goPrevious()}
        pending={packages.pending}
      />
      {canManage ? (
        <button className="button" onClick={() => setOpen(true)} type="button">
          Create package
        </button>
      ) : null}
      {open && canManage ? (
        <form
          aria-labelledby="create-package-title"
          className="grid gap-3 rounded border border-slate-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            const packageType = String(form.get("type") ?? "PT");
            const classTypeId = String(form.get("class_type_id") ?? "");
            setPending(true);
            setError("");
            setNotice("");
            void createServicePackage({
              type: packageType === "CLASS" ? "CLASS" : "PT",
              code: String(form.get("code") ?? ""),
              name: String(form.get("name") ?? ""),
              session_count: Number(form.get("session_count")),
              validity_value: Number(form.get("validity_value")),
              validity_unit: validityUnit(
                String(form.get("validity_unit") ?? ""),
              ),
              price: String(form.get("price") ?? ""),
              ...(packageType === "CLASS" && classTypeId
                ? { class_type_id: classTypeId }
                : {}),
            })
              .then(() => {
                setNotice("Package created.");
                setOpen(false);
                packages.reload();
              })
              .catch((caught: unknown) => {
                setError(
                  phase5Error(caught, "The package could not be created."),
                );
              })
              .finally(() => setPending(false));
          }}
          role="dialog"
        >
          <h3 className="font-semibold" id="create-package-title">
            Create package
          </h3>
          <p className="muted">
            Currency is set by the gym ({currency}). Definitions stay immutable.
          </p>
          <Field label="Type">
            <SelectInput defaultValue="PT" name="type">
              <option value="PT">PT</option>
              <option value="CLASS">CLASS</option>
            </SelectInput>
          </Field>
          <Field label="Code">
            <TextInput name="code" required />
          </Field>
          <Field label="Name">
            <TextInput name="name" required />
          </Field>
          <Field label="Sessions">
            <TextInput min={1} name="session_count" required type="number" />
          </Field>
          <Field label="Validity">
            <TextInput min={1} name="validity_value" required type="number" />
          </Field>
          <Field label="Validity unit">
            <SelectInput defaultValue="MONTH" name="validity_unit">
              <option value="DAY">DAY</option>
              <option value="WEEK">WEEK</option>
              <option value="MONTH">MONTH</option>
              <option value="YEAR">YEAR</option>
            </SelectInput>
          </Field>
          <Field label="Price">
            <TextInput name="price" required />
          </Field>
          <Alert message={classTypes.error} />
          <Field label="Class type for a CLASS package">
            <SelectInput name="class_type_id">
              <option value="">None</option>
              {classTypes.items.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.code} · {row.name}
                </option>
              ))}
            </SelectInput>
          </Field>
          <button className="button" disabled={pending} type="submit">
            Save package
          </button>
        </form>
      ) : null}
    </div>
  );
}
