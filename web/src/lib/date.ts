function zonedWallTime(instant: Date, timeZone: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(instant);
  const values = Object.fromEntries(
    parts
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
  const hour = values.hour === "24" ? 0 : Number(values.hour);
  return Date.UTC(
    Number(values.year),
    Number(values.month) - 1,
    Number(values.day),
    hour,
    Number(values.minute),
    Number(values.second),
  );
}

export function zonedLocalToIso(localValue: string, timeZone: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(localValue);
  if (!match?.[1] || !match[2] || !match[3] || !match[4] || !match[5]) {
    throw new Error("Enter a valid time.");
  }
  const desired = Date.UTC(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3]),
    Number(match[4]),
    Number(match[5]),
    0,
  );
  let instant = desired;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const offset = zonedWallTime(new Date(instant), timeZone) - instant;
    const next = desired - offset;
    if (next === instant) break;
    instant = next;
  }
  return new Date(instant).toISOString();
}

export function formatAppDate(value: string | null | undefined): string {
  if (!value) return "";
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.trim());
  if (!match?.[1] || !match[2] || !match[3]) return value;
  return `${match[3]}-${match[2]}-${match[1]}`;
}

export function formatAppDateTime(
  value: string | null | undefined,
  timeZone?: string,
): string {
  if (!value) return "";
  const calendar = formatAppDate(value);
  if (calendar !== value) return calendar;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const parts = new Intl.DateTimeFormat("en-GB", {
    ...(timeZone ? { timeZone } : {}),
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).formatToParts(date);
  const values = Object.fromEntries(
    parts
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
  const hour = values.hour === "24" ? "00" : values.hour;
  return `${values.day}-${values.month}-${values.year} ${hour}:${values.minute}`;
}

export function formatInstant(value: string | null, timeZone: string): string {
  if (!value) return "Not recorded";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Not recorded";
  return formatAppDateTime(value, timeZone);
}

export function todayInTimezone(timeZone: string): string {
  const parts = new Intl.DateTimeFormat("en", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const values = Object.fromEntries(
    parts.map((part) => [part.type, part.value]),
  );
  return `${values.year}-${values.month}-${values.day}`;
}
