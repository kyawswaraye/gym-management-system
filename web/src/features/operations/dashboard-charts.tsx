"use client";

import { useEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

import type { ChartPoint } from "@/features/operations/dashboard-series";
import { useTx } from "@/i18n/use-tx";

const PAD = { left: 44, right: 12, top: 16, bottom: 32 };
const CHART_BARS = [
  "var(--color-chart-blue)",
  "var(--color-chart-orange)",
  "var(--color-chart-violet)",
  "var(--color-chart-emerald)",
  "var(--color-chart-teal)",
  "var(--color-chart-indigo)",
];

export function TrendChart({
  accent = "var(--color-chart-teal)",
  bands = false,
  empty,
  formatTick,
  formatValue,
  height,
  kind,
  points,
  series,
  variedBars = false,
}: {
  accent?: string;
  bands?: boolean;
  empty: string;
  formatTick?: ((value: number) => string) | undefined;
  formatValue: (value: number) => string;
  height: number;
  kind: "area" | "bar" | "line";
  points: ChartPoint[];
  series: string;
  variedBars?: boolean;
}) {
  const tx = useTx();
  const frameRef = useRef<HTMLDivElement>(null);
  const buttonRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const [frameWidth, setFrameWidth] = useState(560);
  const [active, setActive] = useState<number | null>(null);
  const labelId = useId();
  const fillId = useId();

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const apply = () => {
      const next = Math.max(280, Math.floor(frame.clientWidth));
      setFrameWidth((current) => (current === next ? current : next));
    };
    apply();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(apply);
    observer.observe(frame);
    return () => observer.disconnect();
  }, [points.length]);

  if (!points.length) {
    return <p className="text-body text-muted">{tx(empty)}</p>;
  }

  const plotWidth =
    points.length <= 31
      ? frameWidth
      : Math.max(frameWidth, points.length * 24 + PAD.left + PAD.right);
  const min = Math.min(0, ...points.map((point) => point.value));
  const max = Math.max(0, ...points.map((point) => point.value));
  const scaleMax = niceCeiling(max);
  const scaleMin = min < 0 ? -niceCeiling(Math.abs(min)) : 0;
  const span = scaleMax - scaleMin || 1;
  const innerWidth = plotWidth - PAD.left - PAD.right;
  const innerHeight = height - PAD.top - PAD.bottom;
  const slot = innerWidth / points.length;
  const yOf = (value: number) =>
    PAD.top + ((scaleMax - value) / span) * innerHeight;
  const baseline = yOf(0);
  const tickValues = axisTicks(scaleMin, scaleMax, !formatTick);
  const labelEvery = labelStep(points.length, plotWidth);
  const selected = active === null ? null : points[active];

  function move(next: number) {
    const index = Math.max(0, Math.min(points.length - 1, next));
    setActive(index);
    buttonRefs.current[index]?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      event.preventDefault();
      move(index + 1);
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      event.preventDefault();
      move(index - 1);
    } else if (event.key === "Home") {
      event.preventDefault();
      move(0);
    } else if (event.key === "End") {
      event.preventDefault();
      move(points.length - 1);
    } else if (event.key === "Escape") {
      setActive(null);
    }
  }

  const coordinates = points.map((point, index) => {
    const x = PAD.left + slot * index + slot / 2;
    return { x, y: yOf(point.value) };
  });
  const line = curve(coordinates);
  const area = closedArea(coordinates, baseline);
  const underlays = bands
    ? [
        { color: "var(--color-chart-violet)", factor: 0.42, opacity: 0.2 },
        { color: "var(--color-chart-blue)", factor: 0.7, opacity: 0.24 },
      ]
    : [];

  return (
    <div>
      <div className="w-full overflow-x-auto" ref={frameRef}>
        <div className="relative" style={{ width: plotWidth, height }}>
          <svg
            aria-labelledby={labelId}
            height={height}
            role="img"
            viewBox={`0 0 ${plotWidth} ${height}`}
            width={plotWidth}
          >
            <title id={labelId}>{tx(series)}</title>
            <defs>
              <linearGradient id={fillId} x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stopColor={accent} stopOpacity="0.45" />
                <stop offset="100%" stopColor={accent} stopOpacity="0.02" />
              </linearGradient>
            </defs>
            {tickValues.map((tick) => (
              <g key={tick}>
                <line
                  stroke="var(--color-border)"
                  strokeWidth="1"
                  x1={PAD.left}
                  x2={plotWidth - PAD.right}
                  y1={yOf(tick)}
                  y2={yOf(tick)}
                />
                <text
                  fill="var(--color-muted)"
                  fontSize="12"
                  textAnchor="end"
                  x={PAD.left - 8}
                  y={yOf(tick) + 4}
                >
                  {formatTick ? formatTick(tick) : axisLabel(tick)}
                </text>
              </g>
            ))}
            {kind === "bar"
              ? points.map((point, index) => {
                  const barWidth = Math.min(slot * 0.56, 36);
                  const x = PAD.left + slot * index + (slot - barWidth) / 2;
                  const y = point.value >= 0 ? yOf(point.value) : baseline;
                  const height = Math.max(
                    Math.abs(baseline - yOf(point.value)),
                    0,
                  );
                  return (
                    <rect
                      fill={
                        variedBars
                          ? (CHART_BARS[index % CHART_BARS.length] ?? accent)
                          : accent
                      }
                      opacity={active !== null && index !== active ? 0.35 : 1}
                      height={height}
                      key={point.caption}
                      rx="4"
                      width={barWidth}
                      x={x}
                      y={y}
                    />
                  );
                })
              : null}
            {kind === "area"
              ? underlays.map((band) => (
                  <path
                    d={closedArea(
                      coordinates.map((point) => ({
                        x: point.x,
                        y: baseline - (baseline - point.y) * band.factor,
                      })),
                      baseline,
                    )}
                    fill={band.color}
                    key={band.color}
                    opacity={band.opacity}
                  />
                ))
              : null}
            {kind === "area" ? (
              <path d={area} fill={`url(#${fillId})`} />
            ) : null}
            {kind !== "bar" ? (
              <path
                d={line}
                fill="none"
                stroke={accent}
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth="2.5"
              />
            ) : null}
            {kind !== "bar"
              ? coordinates.map((point, index) => (
                  <circle
                    cx={point.x}
                    cy={point.y}
                    fill={index === active ? "var(--color-surface)" : accent}
                    stroke={accent}
                    strokeWidth={index === active ? 2 : 0}
                    key={points[index]?.caption ?? index}
                    r={index === active ? 5 : 3}
                  />
                ))
              : null}
            {points.map((point, index) =>
              index % labelEvery === 0 ? (
                <text
                  fill="var(--color-muted)"
                  fontSize="12"
                  key={`${point.caption}-label`}
                  textAnchor="middle"
                  x={PAD.left + slot * index + slot / 2}
                  y={height - 10}
                >
                  {point.label}
                </text>
              ) : null,
            )}
          </svg>
          <div
            className="absolute flex"
            style={{
              bottom: PAD.bottom,
              left: PAD.left,
              right: PAD.right,
              top: PAD.top,
            }}
          >
            {points.map((point, index) => (
              <button
                aria-label={`${point.caption}: ${formatValue(point.value)}`}
                aria-pressed={index === active}
                className="h-full min-w-0 flex-1 border-0 bg-transparent p-0"
                key={point.caption}
                onBlur={() =>
                  setActive((current) => (current === index ? null : current))
                }
                onClick={() => setActive(index)}
                onFocus={() => setActive(index)}
                onKeyDown={(event) => onKeyDown(event, index)}
                onMouseEnter={() => setActive(index)}
                onMouseLeave={() =>
                  setActive((current) => (current === index ? null : current))
                }
                ref={(node) => {
                  buttonRefs.current[index] = node;
                }}
                tabIndex={index === (active ?? 0) ? 0 : -1}
                type="button"
              />
            ))}
          </div>
        </div>
      </div>
      <p className="mt-3 flex items-center gap-2 text-caption text-muted">
        <span
          aria-hidden="true"
          className="size-2 rounded-full"
          style={{ background: accent }}
        />
        {tx(series)}
      </p>
      <p
        className={`mt-2 min-h-8 rounded-theme-md px-3 py-2 text-caption ${
          selected ? "bg-inset text-text" : "text-muted"
        }`}
      >
        {selected
          ? `${selected.caption} · ${formatValue(selected.value)}`
          : tx("Select a point to see its total.")}
      </p>
    </div>
  );
}

export function Sparkline({
  color = "var(--color-chart-emerald)",
  values,
}: {
  color?: string;
  values: number[];
}) {
  const width = 96;
  const height = 32;
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const coordinates = values.map((value, index) => ({
    x: (index / (values.length - 1)) * width,
    y: height - 3 - ((value - min) / span) * (height - 6),
  }));
  const line = curve(coordinates);
  const area = `${line} L${width} ${height} L0 ${height} Z`;

  return (
    <svg
      aria-hidden="true"
      className="h-8 w-24 shrink-0"
      viewBox={`0 0 ${width} ${height}`}
    >
      <path d={area} fill={color} opacity="0.18" />
      <path
        d={line}
        fill="none"
        stroke={color}
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.5"
      />
    </svg>
  );
}

export function ArcGauge({ max, value }: { max: number; value: number }) {
  const radius = 34;
  const circumference = 2 * Math.PI * radius;
  const track = circumference * 0.75;
  const ratio = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0;
  return (
    <svg aria-hidden="true" className="size-24" viewBox="0 0 100 100">
      <circle
        cx="50"
        cy="54"
        fill="none"
        r={radius}
        stroke="var(--color-inset)"
        strokeDasharray={`${track} ${circumference}`}
        strokeLinecap="round"
        strokeWidth="10"
        transform="rotate(135 50 54)"
      />
      <circle
        cx="50"
        cy="54"
        fill="none"
        r={radius}
        stroke="var(--color-chart-orange)"
        strokeDasharray={`${track * ratio} ${circumference}`}
        strokeLinecap="round"
        strokeWidth="10"
        transform="rotate(135 50 54)"
      />
      <text
        fill="var(--color-text)"
        fontSize="16"
        fontWeight="600"
        textAnchor="middle"
        x="50"
        y="58"
      >
        {value}
      </text>
    </svg>
  );
}

export function ShareDonut({
  active,
  parts,
  size = "md",
}: {
  active: number | null;
  parts: { color: string; value: number }[];
  size?: "md" | "sm";
}) {
  const slices = parts
    .map((part, index) => ({ ...part, index }))
    .filter((part) => part.value > 0);
  const total = slices.reduce((sum, part) => sum + part.value, 0);
  const radius = 32;
  const circumference = 2 * Math.PI * radius;
  const gap = slices.length > 1 ? 2 : 0;
  let consumed = 0;

  return (
    <div
      className={
        size === "sm" ? "mx-auto size-24" : "mx-auto size-40 sm:size-44"
      }
    >
      <svg aria-hidden="true" className="size-full" viewBox="0 0 100 100">
        <circle
          cx="50"
          cy="50"
          fill="none"
          r={radius}
          stroke="var(--color-inset)"
          strokeWidth="14"
        />
        {total > 0
          ? slices.map((part) => {
              const sweep = (part.value / total) * circumference;
              const length = sweep > gap + 1 ? sweep - gap : sweep;
              const offset = consumed;
              consumed += sweep;
              const dim = active !== null && active !== part.index;
              return (
                <circle
                  cx="50"
                  cy="50"
                  fill="none"
                  key={part.index}
                  opacity={dim ? 0.28 : 1}
                  r={radius}
                  stroke={part.color}
                  strokeDasharray={`${length} ${circumference - length}`}
                  strokeDashoffset={-offset}
                  strokeWidth="14"
                  transform="rotate(-90 50 50)"
                />
              );
            })
          : null}
      </svg>
    </div>
  );
}

function closedArea(
  points: { x: number; y: number }[],
  baseline: number,
): string {
  const line = curve(points);
  const first = points[0];
  const last = points[points.length - 1];
  if (!first || !last) return "";
  return `${line} L${last.x} ${baseline} L${first.x} ${baseline} Z`;
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

function curve(points: { x: number; y: number }[]): string {
  const first = points[0];
  if (!first) return "";
  if (points.length === 1) return `M${first.x} ${first.y}`;
  let path = `M${first.x} ${first.y}`;
  for (let index = 0; index < points.length - 1; index += 1) {
    const previous = points[index - 1] ?? points[index];
    const current = points[index];
    const next = points[index + 1];
    const after = points[index + 2] ?? next;
    if (!previous || !current || !next || !after) continue;
    const low = Math.min(current.y, next.y);
    const high = Math.max(current.y, next.y);
    const controlStartX = current.x + (next.x - previous.x) / 6;
    const controlStartY = clamp(
      current.y + (next.y - previous.y) / 6,
      low,
      high,
    );
    const controlEndX = next.x - (after.x - current.x) / 6;
    const controlEndY = clamp(next.y - (after.y - current.y) / 6, low, high);
    path += ` C${controlStartX} ${controlStartY},${controlEndX} ${controlEndY},${next.x} ${next.y}`;
  }
  return path;
}

function niceCeiling(value: number): number {
  if (value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  const scaled = value / power;
  const nice = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10;
  return nice * power;
}

function axisTicks(min: number, max: number, integers: boolean): number[] {
  const span = max - min || 1;
  const rough = integers ? Math.max(1, Math.ceil(span / 4)) : span / 4;
  const step = integers ? Math.max(1, niceCeiling(rough)) : niceCeiling(rough);
  const ticks: number[] = [];
  const start = Math.ceil(min / step) * step;
  for (let value = start; value <= max + step / 100; value += step) {
    ticks.push(Number(value.toFixed(6)));
    if (ticks.length > 6) break;
  }
  if (!ticks.includes(0) && min <= 0 && max >= 0) ticks.push(0);
  return ticks;
}

function axisLabel(value: number): string {
  if (Math.abs(value) >= 1000) {
    const compact = value / 1000;
    return Number.isInteger(compact) ? `${compact}k` : `${compact.toFixed(1)}k`;
  }
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

function labelStep(count: number, plotWidth: number): number {
  const inner = Math.max(plotWidth - PAD.left - PAD.right, 1);
  const maxLabels = Math.max(2, Math.floor(inner / 48));
  return Math.max(1, Math.ceil(count / maxLabels));
}
