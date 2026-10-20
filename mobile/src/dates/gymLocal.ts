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

export function formatGymDate(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) {
    return value;
  }
  const monthIndex = Number(match[2]) - 1;
  const month = MONTHS[monthIndex];
  if (!month) {
    return value;
  }
  return `${Number(match[3])} ${month} ${match[1]}`;
}

export function formatGymDateTime(value: string): string {
  const match =
    /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/.exec(
      value,
    );
  if (!match || !match[1] || !match[2] || !match[3]) {
    return value;
  }
  return `${formatGymDate(match[1])} ${match[2]} ${match[3]}`;
}

export function formatServerMoney(
  amount: string | null,
  currency: string,
): string {
  if (amount === null) {
    return "Unavailable";
  }
  return `${currency} ${amount}`;
}

export function formatRemainingSessions(remainingSessions: number): string {
  return String(remainingSessions);
}
