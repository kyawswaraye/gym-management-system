import { tx } from "../i18n/translate";

export type CalendarDate = {
  year: number;
  month: number;
  day: number;
};

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

export const SCHEDULE_WINDOW_DAYS = 42;
export const FALLBACK_GYM_TIMEZONE = "Asia/Yangon";

export function calendarDateFromLocal(now: Date): CalendarDate {
  return {
    year: now.getFullYear(),
    month: now.getMonth() + 1,
    day: now.getDate(),
  };
}

export function formatCalendarDate(value: CalendarDate): string {
  const month = String(value.month).padStart(2, "0");
  const day = String(value.day).padStart(2, "0");
  return `${value.year}-${month}-${day}`;
}

export function addCalendarDays(
  value: CalendarDate,
  days: number,
): CalendarDate {
  const utc = new Date(Date.UTC(value.year, value.month - 1, value.day + days));
  return {
    year: utc.getUTCFullYear(),
    month: utc.getUTCMonth() + 1,
    day: utc.getUTCDate(),
  };
}

export function scheduleWindow(today: CalendarDate): {
  date_from: string;
  date_to: string;
} {
  return {
    date_from: formatCalendarDate(today),
    date_to: formatCalendarDate(
      addCalendarDays(today, SCHEDULE_WINDOW_DAYS - 1),
    ),
  };
}

export function formatGymMonthLabel(year: number, month: number): string {
  const name = MONTHS[month - 1];
  if (!name) {
    return "";
  }
  return `${tx(name)} ${year}`;
}

export function formatGymDate(value: string): string {
  if (typeof value !== "string") {
    return "";
  }
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) {
    return value;
  }
  const monthIndex = Number(match[2]) - 1;
  const month = MONTHS[monthIndex];
  if (!month) {
    return value;
  }
  return `${match[3]}-${match[2]}-${match[1]}`;
}

function gymWallParts(
  value: string,
  timeZone: string,
): { date: string; time: string } | null {
  const instant = new Date(value);
  if (Number.isNaN(instant.getTime())) {
    return null;
  }
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).formatToParts(instant);
  const values = Object.fromEntries(
    parts
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
  const hour = values.hour === "24" ? "00" : values.hour;
  if (
    !values.year ||
    !values.month ||
    !values.day ||
    !hour ||
    !values.minute
  ) {
    return null;
  }
  return {
    date: `${values.year}-${values.month}-${values.day}`,
    time: `${hour}:${values.minute}`,
  };
}

export function formatGymTime(
  value: string,
  timeZone: string = FALLBACK_GYM_TIMEZONE,
): string {
  if (typeof value !== "string" || !value) {
    return "";
  }
  const wall = gymWallParts(value, timeZone);
  return wall?.time ?? "";
}

export function formatGymDateTime(
  value: string,
  timeZone: string = FALLBACK_GYM_TIMEZONE,
): string {
  if (typeof value !== "string" || !value) {
    return "";
  }
  const wall = gymWallParts(value, timeZone);
  if (!wall) {
    return value;
  }
  return `${formatGymDate(wall.date)} ${wall.time}`;
}

export function gymLocalCalendarDate(
  value: string,
  timeZone: string = FALLBACK_GYM_TIMEZONE,
): string {
  if (typeof value !== "string" || !value) {
    return "";
  }
  return gymWallParts(value, timeZone)?.date ?? "";
}

export function formatServerMoney(
  amount: string | null,
  currency: string,
): string {
  if (amount === null) {
    return tx("Unavailable");
  }
  return `${tx(currency)} ${amount}`;
}

export function formatRemainingSessions(remainingSessions: unknown): string {
  if (
    typeof remainingSessions !== "number" ||
    !Number.isFinite(remainingSessions)
  ) {
    return "";
  }
  return String(remainingSessions);
}
