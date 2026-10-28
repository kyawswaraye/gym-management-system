"use client";
import { useTx } from "@/i18n/use-tx";

import { useEffect, useRef, useState } from "react";

import { listProducts } from "@/features/finance/client";
import { financeError } from "@/features/finance/errors";
import { Field } from "@/features/finance/ui";
import type { Product } from "@/types/api";

const SEARCH_LIMIT = 10;
const SEARCH_DEBOUNCE_MS = 250;

export function productChoiceLabel(product: Product): string {
  const barcode = product.barcode ? ` · ${product.barcode}` : "";
  return `${product.sku} — ${product.name}${barcode}`;
}

function compactToken(value: string): string {
  return value.replace(/\s+/g, "").toUpperCase();
}

export function ProductSearch({
  selected,
  onSelect,
  disabled,
  label = "Product",
  activeOnly = true,
  excludeIds = [],
  showEmptySelected = true,
  emptySelectedLabel = "Select a product",
}: {
  selected: Product | null;
  onSelect: (product: Product) => void;
  disabled?: boolean;
  label?: string;
  activeOnly?: boolean;
  excludeIds?: readonly string[];
  showEmptySelected?: boolean;
  emptySelectedLabel?: string;
}) {
  const tx = useTx();
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<Product[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const searchVersion = useRef(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const excludeKey = excludeIds.join("\0");

  const trimmed = search.trim();
  const panelOpen = trimmed.length > 0;

  useEffect(() => {
    if (!trimmed) {
      setResults([]);
      setHasMore(false);
      setError("");
      setPending(false);
      return;
    }

    const version = ++searchVersion.current;
    const excluded = new Set(excludeKey ? excludeKey.split("\0") : []);
    setPending(true);
    setError("");
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const page = await listProducts({
            search: trimmed,
            ...(activeOnly ? { is_active: "true" } : {}),
            limit: String(SEARCH_LIMIT),
          });
          if (version !== searchVersion.current) return;
          const visible = page.data.filter(
            (product) => !excluded.has(product.id),
          );
          const compact = compactToken(trimmed);
          const exact = visible.find(
            (product) =>
              (product.barcode != null && product.barcode === compact) ||
              product.sku === compact,
          );
          if (exact) {
            onSelectRef.current(exact);
            setSearch("");
            setResults([]);
            setHasMore(false);
            setError("");
            setPending(false);
            searchVersion.current += 1;
            return;
          }
          setResults(visible);
          setHasMore(
            Boolean(page.meta.has_more) || page.data.length > visible.length,
          );
          setError(visible.length === 0 ? tx("No matching products.") : "");
        } catch (caught) {
          if (version !== searchVersion.current) return;
          setResults([]);
          setHasMore(false);
          setError(financeError(caught, tx("Unable to search products.")));
        } finally {
          if (version === searchVersion.current) setPending(false);
        }
      })();
    }, SEARCH_DEBOUNCE_MS);

    return () => {
      window.clearTimeout(timer);
      searchVersion.current += 1;
    };
  }, [activeOnly, excludeKey, trimmed, tx]);

  useEffect(() => {
    if (!panelOpen) return undefined;

    function clearPanel() {
      setSearch("");
      setResults([]);
      setHasMore(false);
      setError("");
      setPending(false);
      searchVersion.current += 1;
    }

    function onPointerDown(event: MouseEvent) {
      const root = rootRef.current;
      if (!root || root.contains(event.target as Node)) return;
      clearPanel();
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      clearPanel();
    }

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [panelOpen]);

  function choose(product: Product) {
    onSelect(product);
    setSearch("");
    setResults([]);
    setHasMore(false);
    setError("");
    setPending(false);
    searchVersion.current += 1;
  }

  return (
    <div className="grid gap-2" ref={rootRef}>
      <div className="relative z-20">
        <Field label={label}>
          <input
            autoComplete="off"
            className="input"
            disabled={disabled}
            maxLength={100}
            name="product_search"
            onChange={(event) => setSearch(event.target.value)}
            placeholder={tx("Name, SKU, barcode, or category")}
            value={search}
          />
        </Field>
        {panelOpen ? (
          <div className="absolute top-full right-0 left-0 z-30 mt-1 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-lg">
            {pending ? (
              <p className="px-3 py-2 text-sm text-slate-500" role="status">
                {tx("Searching products.")}
              </p>
            ) : null}
            {!pending && error ? (
              <p className="error px-3 py-2" role="alert">
                {tx(error)}
              </p>
            ) : null}
            {!pending && results.length > 0 ? (
              <ul
                aria-label={tx("Matching products")}
                className="grid max-h-40 gap-0.5 overflow-y-auto overscroll-contain p-1"
              >
                {results.map((product) => (
                  <li key={product.id}>
                    <button
                      aria-pressed={selected?.id === product.id}
                      className="w-full rounded-md px-2 py-1.5 text-left text-sm font-semibold text-slate-700 hover:bg-slate-100 aria-pressed:bg-amber-100 aria-pressed:text-amber-900"
                      disabled={disabled}
                      onClick={() => choose(product)}
                      type="button"
                    >
                      {productChoiceLabel(product)}
                      <span className="mt-0.5 block text-xs font-medium text-slate-500">
                        {product.sale_price} · {product.unit}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
            {!pending && hasMore ? (
              <p className="border-t border-slate-100 px-3 py-2 text-xs text-slate-500">
                {tx("Showing the first matches. Type more to narrow the list.")}
              </p>
            ) : null}
          </div>
        ) : null}
      </div>
      {selected ? (
        <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-800">
          {tx("Selected product: {name}", {
            name: productChoiceLabel(selected),
          })}
        </p>
      ) : showEmptySelected ? (
        <p className="rounded-lg border border-dashed border-slate-300 px-3 py-2 text-sm text-slate-500">
          {tx(emptySelectedLabel)}
        </p>
      ) : null}
    </div>
  );
}
