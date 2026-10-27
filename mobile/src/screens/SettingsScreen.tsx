import { LanguageSwitch } from "../i18n/LanguageSwitch";
import { useTx } from "../i18n/useTx";
import { useState } from "react";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { View } from "react-native";

import { version } from "../../package.json";
import { useAuth, useSetLocale } from "../app/providers";
import { createSecureStore } from "../auth/secureStore";
import type { AccountStackParamList } from "../navigation/types";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { MenuRow } from "../ui/MenuRow";
import { NestedScreen } from "../ui/NestedScreen";
import { Text } from "../ui/Text";
import { colors, space } from "../ui/theme";

const store = createSecureStore();

type Props = NativeStackScreenProps<AccountStackParamList, "Settings">;

export function SettingsScreen({ navigation }: Props) {
  const tx = useTx();
  const setLocale = useSetLocale();
  const auth = useAuth();
  const [message, setMessage] = useState<string | null>(null);
  const [biometric, setBiometric] = useState(false);
  const [busy, setBusy] = useState(false);

  return (
    <NestedScreen title={tx("Settings")}>
      <Card>
        <Text style={{ fontSize: 18, fontWeight: "700" }}>
          {tx("GymPilot MM")}
        </Text>
        <Text style={{ color: colors.muted }}>{version}</Text>
        <Text style={{ color: colors.muted }}>
          {tx("Android 8 and newer, iOS 16 and newer.")}
        </Text>
        <LanguageSwitch onChange={(value) => void setLocale(value)} />
      </Card>
      <Card padded={false}>
        <View style={{ paddingHorizontal: space.md }}>
          <MenuRow
            chevron={false}
            hint="Requires biometrics before the saved session can be read"
            label={biometric ? "Biometric lock on" : "Use biometric lock"}
            onPress={() => {
              const next = !biometric;
              void store.setBiometricGate(next).then(() => setBiometric(next));
            }}
          />
        </View>
        <View
          style={{
            borderTopColor: colors.border,
            borderTopWidth: 1,
            paddingHorizontal: space.md,
          }}
        >
          <MenuRow
            hint="Opens push consent for this phone"
            label="Notification consent"
            onPress={() => navigation.navigate("NotificationSettings")}
          />
        </View>
        <View
          style={{
            borderTopColor: colors.border,
            borderTopWidth: 1,
            paddingHorizontal: space.md,
          }}
        >
          <MenuRow
            hint="Opens the privacy details"
            label="Privacy"
            onPress={() =>
              navigation.navigate("Legal", { document: "privacy" })
            }
          />
        </View>
        <View
          style={{
            borderTopColor: colors.border,
            borderTopWidth: 1,
            paddingHorizontal: space.md,
          }}
        >
          <MenuRow
            hint="Opens the terms"
            label="Terms"
            onPress={() => navigation.navigate("Legal", { document: "terms" })}
          />
        </View>
      </Card>
      <Button
        disabled={busy}
        hint="Revokes this session and removes it from the phone"
        label="Sign out"
        variant="danger"
        onPress={async () => {
          setBusy(true);
          try {
            const result = await auth.logout();
            setMessage(
              result.serverConfirmed
                ? tx("Signed out.")
                : tx(
                    "This phone is signed out. The server session could not be confirmed.",
                  ),
            );
          } catch (error) {
            setMessage(
              error instanceof Error ? error.message : tx("Sign out failed."),
            );
          } finally {
            setBusy(false);
          }
        }}
      />
      {message ? (
        <Text accessibilityRole="alert" style={{ color: colors.muted }}>
          {message}
        </Text>
      ) : null}
    </NestedScreen>
  );
}
