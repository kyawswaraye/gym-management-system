import { useTx } from "../i18n/useTx";
import { useMemo, useState } from "react";
import { FlatList, Modal, Pressable, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { colors, radius, space, touchTarget } from "./theme";
import { Text } from "./Text";

export type SelectOption = {
  value: string;
  label: string;
};

export function SelectField({
  label,
  value,
  options,
  placeholder,
  onChange,
  disabled,
  error,
}: {
  label: string;
  value: string;
  options: readonly SelectOption[];
  placeholder: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  error?: string;
}) {
  const tx = useTx();
  const insets = useSafeAreaInsets();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const labelText = tx(label);
  const placeholderText = tx(placeholder);
  const errorText = error ? tx(error) : undefined;
  const selected = options.find((option) => option.value === value);
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) {
      return options;
    }
    return options.filter((option) =>
      option.label.toLowerCase().includes(needle),
    );
  }, [options, query]);

  return (
    <View style={{ gap: 4 }}>
      <Text style={{ fontWeight: "700" }}>{labelText}</Text>
      <Pressable
        accessibilityLabel={labelText}
        accessibilityRole="button"
        accessibilityState={{ disabled: Boolean(disabled) }}
        disabled={disabled}
        onPress={() => {
          setQuery("");
          setOpen(true);
        }}
        style={{
          backgroundColor: colors.surface,
          borderColor: error ? colors.danger : colors.border,
          borderRadius: radius.md,
          borderWidth: 1,
          justifyContent: "center",
          minHeight: touchTarget,
          opacity: disabled ? 0.55 : 1,
          paddingHorizontal: 12,
        }}
      >
        <Text style={{ color: selected ? colors.text : colors.muted }}>
          {selected?.label ?? placeholderText}
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
              maxHeight: "80%",
              paddingTop: space.md,
              paddingBottom: insets.bottom + space.md,
            }}
          >
            <View
              style={{
                alignItems: "center",
                flexDirection: "row",
                justifyContent: "space-between",
                paddingHorizontal: space.md,
              }}
            >
              <Text style={{ fontSize: 18, fontWeight: "700" }}>
                {labelText}
              </Text>
              <Pressable
                accessibilityLabel={tx("Close")}
                onPress={() => setOpen(false)}
                style={{ justifyContent: "center", minHeight: touchTarget }}
              >
                <Text style={{ color: colors.primary, fontWeight: "700" }}>
                  {tx("Close")}
                </Text>
              </Pressable>
            </View>
            <TextInput
              accessibilityLabel={tx("Search")}
              autoCorrect={false}
              onChangeText={setQuery}
              placeholder={tx("Search")}
              placeholderTextColor={colors.muted}
              value={query}
              style={{
                borderColor: colors.border,
                borderRadius: radius.md,
                borderWidth: 1,
                color: colors.text,
                margin: space.md,
                minHeight: touchTarget,
                paddingHorizontal: 12,
              }}
            />
            <FlatList
              data={visible}
              keyboardShouldPersistTaps="handled"
              keyExtractor={(item) => item.value}
              ListEmptyComponent={
                <Text
                  style={{ color: colors.muted, paddingHorizontal: space.md }}
                >
                  {tx("No matches.")}
                </Text>
              }
              renderItem={({ item }) => {
                const active = item.value === value;
                return (
                  <Pressable
                    accessibilityRole="button"
                    accessibilityState={{ selected: active }}
                    onPress={() => {
                      onChange(item.value);
                      setOpen(false);
                    }}
                    style={{
                      backgroundColor: active
                        ? colors.primarySoft
                        : colors.surface,
                      minHeight: touchTarget,
                      justifyContent: "center",
                      paddingHorizontal: space.md,
                      paddingVertical: 12,
                    }}
                  >
                    <Text
                      style={{
                        color: active ? colors.primary : colors.text,
                        fontWeight: active ? "700" : "400",
                      }}
                    >
                      {item.label}
                    </Text>
                  </Pressable>
                );
              }}
            />
          </View>
        </View>
      </Modal>
    </View>
  );
}
