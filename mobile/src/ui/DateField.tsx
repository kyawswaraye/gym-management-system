import { useTx } from "../i18n/useTx";
import { useMemo, useState } from "react";
import { Modal, Pressable, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { formatGymDate, formatGymMonthLabel } from "../dates/gymLocal";
import { colors, radius, space, touchTarget } from "./theme";
import { Text } from "./Text";

const WEEKDAYS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"] as const;

function parseIso(
  value: string,
): { year: number; month: number; day: number } | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match?.[1] || !match[2] || !match[3]) {
    return null;
  }
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const probe = new Date(Date.UTC(year, month - 1, day));
  if (
    probe.getUTCFullYear() !== year ||
    probe.getUTCMonth() !== month - 1 ||
    probe.getUTCDate() !== day
  ) {
    return null;
  }
  return { year, month, day };
}

function isoFrom(year: number, month: number, day: number): string {
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

function shiftMonth(
  year: number,
  month: number,
  delta: number,
): { year: number; month: number } {
  const next = new Date(Date.UTC(year, month - 1 + delta, 1));
  return { year: next.getUTCFullYear(), month: next.getUTCMonth() + 1 };
}

export function DateField({
  label,
  value,
  onChange,
  error,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
}) {
  const tx = useTx();
  const insets = useSafeAreaInsets();
  const [open, setOpen] = useState(false);
  const today = useMemo(() => {
    const now = new Date();
    return {
      year: now.getFullYear(),
      month: now.getMonth() + 1,
      day: now.getDate(),
    };
  }, []);
  const selected = parseIso(value);
  const [cursor, setCursor] = useState(() => {
    if (selected) {
      return { year: selected.year, month: selected.month };
    }
    return shiftMonth(today.year, today.month, -12 * 25);
  });
  const labelText = tx(label);
  const errorText = error ? tx(error) : undefined;
  const monthLabel = formatGymMonthLabel(cursor.year, cursor.month);
  const firstWeekday = new Date(
    Date.UTC(cursor.year, cursor.month - 1, 1),
  ).getUTCDay();
  const days = new Date(Date.UTC(cursor.year, cursor.month, 0)).getUTCDate();
  const cells: Array<number | null> = [
    ...Array.from({ length: firstWeekday }, () => null),
    ...Array.from({ length: days }, (_, index) => index + 1),
  ];

  function choose(day: number) {
    const next = isoFrom(cursor.year, cursor.month, day);
    if (next > isoFrom(today.year, today.month, today.day)) {
      return;
    }
    onChange(next);
    setOpen(false);
  }

  return (
    <View style={{ gap: 4 }}>
      <Text style={{ fontWeight: "700" }}>{labelText}</Text>
      <Pressable
        accessibilityHint={tx("Choose a date")}
        accessibilityLabel={labelText}
        accessibilityRole="button"
        onPress={() => {
          if (selected) {
            setCursor({ year: selected.year, month: selected.month });
          }
          setOpen(true);
        }}
        style={{
          backgroundColor: colors.surface,
          borderColor: error ? colors.danger : colors.border,
          borderRadius: radius.md,
          borderWidth: 1,
          justifyContent: "center",
          minHeight: touchTarget,
          paddingHorizontal: 12,
        }}
      >
        <Text style={{ color: value ? colors.text : colors.muted }}>
          {value ? formatGymDate(value) : tx("Choose a date")}
        </Text>
      </Pressable>
      {errorText ? (
        <Text accessibilityRole="alert" style={{ color: colors.danger }}>
          {errorText}
        </Text>
      ) : null}
      <Modal
        animationType="slide"
        transparent
        visible={open}
        onRequestClose={() => setOpen(false)}
      >
        <View
          style={{
            backgroundColor: "rgba(5,5,5,0.35)",
            flex: 1,
            justifyContent: "flex-end",
          }}
        >
          <View
            style={{
              backgroundColor: colors.surface,
              borderTopLeftRadius: radius.md,
              borderTopRightRadius: radius.md,
              gap: space.md,
              padding: space.md,
              paddingBottom: insets.bottom + space.md,
            }}
          >
            <View
              style={{
                alignItems: "center",
                flexDirection: "row",
                justifyContent: "space-between",
              }}
            >
              <Text style={{ fontSize: 18, fontWeight: "700" }}>
                {tx("Choose a date")}
              </Text>
              <Pressable
                accessibilityLabel={tx("Close")}
                onPress={() => setOpen(false)}
                style={{ minHeight: touchTarget, justifyContent: "center" }}
              >
                <Text style={{ color: colors.primary, fontWeight: "700" }}>
                  {tx("Close")}
                </Text>
              </Pressable>
            </View>
            <View
              style={{
                alignItems: "center",
                flexDirection: "row",
                justifyContent: "space-between",
              }}
            >
              <Pressable
                accessibilityLabel={tx("Previous year")}
                onPress={() =>
                  setCursor((current) =>
                    shiftMonth(current.year, current.month, -12),
                  )
                }
                style={navButton}
              >
                <Text style={{ fontWeight: "700" }}>«</Text>
              </Pressable>
              <Pressable
                accessibilityLabel={tx("Previous month")}
                onPress={() =>
                  setCursor((current) =>
                    shiftMonth(current.year, current.month, -1),
                  )
                }
                style={navButton}
              >
                <Text style={{ fontWeight: "700" }}>‹</Text>
              </Pressable>
              <Text style={{ fontWeight: "700" }}>{monthLabel}</Text>
              <Pressable
                accessibilityLabel={tx("Next month")}
                onPress={() =>
                  setCursor((current) =>
                    shiftMonth(current.year, current.month, 1),
                  )
                }
                style={navButton}
              >
                <Text style={{ fontWeight: "700" }}>›</Text>
              </Pressable>
              <Pressable
                accessibilityLabel={tx("Next year")}
                onPress={() =>
                  setCursor((current) =>
                    shiftMonth(current.year, current.month, 12),
                  )
                }
                style={navButton}
              >
                <Text style={{ fontWeight: "700" }}>»</Text>
              </Pressable>
            </View>
            <View style={{ flexDirection: "row" }}>
              {WEEKDAYS.map((day) => (
                <Text
                  key={day}
                  style={{
                    color: colors.muted,
                    flex: 1,
                    fontSize: 13,
                    textAlign: "center",
                  }}
                >
                  {tx(day)}
                </Text>
              ))}
            </View>
            <View style={{ flexDirection: "row", flexWrap: "wrap" }}>
              {cells.map((day, index) => {
                const iso = day ? isoFrom(cursor.year, cursor.month, day) : "";
                const future =
                  Boolean(day) &&
                  iso > isoFrom(today.year, today.month, today.day);
                const active =
                  selected?.year === cursor.year &&
                  selected.month === cursor.month &&
                  selected.day === day;
                return (
                  <Pressable
                    key={`${cursor.year}-${cursor.month}-${index}`}
                    accessibilityRole="button"
                    accessibilityState={{
                      disabled: !day || future,
                      selected: active,
                    }}
                    disabled={!day || future}
                    onPress={() => {
                      if (day) {
                        choose(day);
                      }
                    }}
                    style={{
                      alignItems: "center",
                      height: touchTarget,
                      justifyContent: "center",
                      width: `${100 / 7}%`,
                    }}
                  >
                    {day ? (
                      <View
                        style={{
                          alignItems: "center",
                          backgroundColor: active
                            ? colors.primary
                            : "transparent",
                          borderRadius: 18,
                          height: 36,
                          justifyContent: "center",
                          width: 36,
                        }}
                      >
                        <Text
                          style={{
                            color: future
                              ? colors.border
                              : active
                                ? colors.onPrimary
                                : colors.text,
                            fontWeight: "600",
                          }}
                        >
                          {String(day)}
                        </Text>
                      </View>
                    ) : null}
                  </Pressable>
                );
              })}
            </View>
            <Pressable
              accessibilityRole="button"
              onPress={() => {
                onChange("");
                setOpen(false);
              }}
              style={{
                alignSelf: "flex-start",
                minHeight: touchTarget,
                justifyContent: "center",
              }}
            >
              <Text style={{ color: colors.danger, fontWeight: "700" }}>
                {tx("Clear")}
              </Text>
            </Pressable>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const navButton = {
  alignItems: "center" as const,
  height: touchTarget,
  justifyContent: "center" as const,
  width: touchTarget,
};
