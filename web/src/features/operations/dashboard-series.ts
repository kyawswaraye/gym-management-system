export type DatedValue = {
  date: string;
  value: number;
};

export type ChartPoint = {
  label: string;
  caption: string;
  value: number;
};

export type YearMonth = {
  year: number;
  month: number;
};

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

export function parseIsoDate(
  value: string,
): { year: number; month: number; day: number } | null {
  const match = ISO_DATE.exec(value);
  if (!match?.[1] || !match[2] || !match[3]) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  if (
    parsed.getUTCFullYear() !== year ||
    parsed.getUTCMonth() !== month - 1 ||
    parsed.getUTCDate() !== day
  ) {
    return null;
  }
  return { year, month, day };
}

export function isMonthEnd(value: string): boolean {
  const parsed = parseIsoDate(value);
  if (!parsed) return false;
  const last = new Date(Date.UTC(parsed.year, parsed.month, 0)).getUTCDate();
  return parsed.day === last;
}

export function monthsCovering(from: string, to: string): YearMonth[] {
  const start = parseIsoDate(from);
  const end = parseIsoDate(to);
  if (!start || !end || from > to) return [];
  const months: YearMonth[] = [];
  let year = start.year;
  let month = start.month;
  while (year < end.year || (year === end.year && month <= end.month)) {
    months.push({ year, month });
    month += 1;
    if (month > 12) {
      month = 1;
      year += 1;
    }
    if (months.length > 14) break;
  }
  return months;
}

export function previousMonth(year: number, month: number): YearMonth {
  return month === 1
    ? { year: year - 1, month: 12 }
    : { year, month: month - 1 };
}

export function monthKey(year: number, month: number): string {
  return `${year}-${String(month).padStart(2, "0")}`;
}

export function weekStart(iso: string): string | null {
  const parsed = parseIsoDate(iso);
  if (!parsed) return null;
  const date = new Date(Date.UTC(parsed.year, parsed.month - 1, parsed.day));
  const weekday = date.getUTCDay();
  const mondayOffset = weekday === 0 ? 6 : weekday - 1;
  date.setUTCDate(date.getUTCDate() - mondayOffset);
  return date.toISOString().slice(0, 10);
}

export function shouldBucketWeekly(dayCount: number): boolean {
  return dayCount > 31;
}

export function toChartPoints(
  days: DatedValue[],
  weekly: boolean,
  weekCaption: (start: string) => string,
): ChartPoint[] {
  const sorted = [...days].sort((left, right) =>
    left.date.localeCompare(right.date),
  );
  if (!weekly) {
    return sorted.map((day) => ({
      label: day.date.slice(5),
      caption: day.date,
      value: day.value,
    }));
  }
  const groups = new Map<string, number>();
  for (const day of sorted) {
    const start = weekStart(day.date);
    if (!start) continue;
    groups.set(start, (groups.get(start) ?? 0) + day.value);
  }
  return [...groups.entries()].map(([start, value]) => ({
    label: start.slice(5),
    caption: weekCaption(start),
    value,
  }));
}

export function moneyCents(value: string): number {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return 0;
  return Math.round(amount * 100);
}

export function formatMoneyCents(cents: number, currency: string): string {
  const sign = cents < 0 ? "-" : "";
  const absolute = Math.abs(cents);
  const major = Math.floor(absolute / 100);
  const minor = String(absolute % 100).padStart(2, "0");
  return `${sign}${major}.${minor} ${currency}`;
}

export function rowsOf(value: unknown): Record<string, unknown>[] | null {
  if (typeof value !== "object" || value === null || !("rows" in value)) {
    return null;
  }
  const rows = value.rows;
  if (!Array.isArray(rows)) return null;
  const parsed: Record<string, unknown>[] = [];
  for (const row of rows) {
    if (typeof row !== "object" || row === null) return null;
    parsed.push(row as Record<string, unknown>);
  }
  return parsed;
}
