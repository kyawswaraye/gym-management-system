import { useState } from "react";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import { version } from "../../package.json";
import { useAuth } from "../app/providers";
import { createSecureStore } from "../auth/secureStore";
import type { AccountStackParamList } from "../navigation/types";
import { Button } from "../ui/Button";
import { Screen } from "../ui/Screen";
import { Text } from "../ui/Text";

const store = createSecureStore();

type Props = NativeStackScreenProps<AccountStackParamList, "Settings">;

export function SettingsScreen({ navigation }: Props) {
  const auth = useAuth();
  const [message, setMessage] = useState<string | null>(null);
  const [biometric, setBiometric] = useState(false);
  const [busy, setBusy] = useState(false);

  return (
    <Screen title="Settings">
      <Text>Gym Member {version}</Text>
      <Text>Android 8 and newer, iOS 16 and newer.</Text>
      <Button
        label={biometric ? "Biometric lock on" : "Use biometric lock"}
        hint="Requires biometrics before the saved session can be read"
        variant="secondary"
        onPress={async () => {
          const next = !biometric;
          await store.setBiometricGate(next);
          setBiometric(next);
        }}
      />
      <Button
        label="Notification consent"
        hint="Opens push consent for this phone"
        variant="secondary"
        onPress={() => navigation.navigate("NotificationSettings")}
      />
      <Button
        label="Privacy"
        hint="Opens the privacy details"
        variant="secondary"
        onPress={() => navigation.navigate("Legal", { document: "privacy" })}
      />
      <Button
        label="Terms"
        hint="Opens the terms"
        variant="secondary"
        onPress={() => navigation.navigate("Legal", { document: "terms" })}
      />
      <Button
        label="Sign out"
        hint="Revokes this session and removes it from the phone"
        variant="danger"
        disabled={busy}
        onPress={async () => {
          setBusy(true);
          try {
            const result = await auth.logout();
            setMessage(
              result.serverConfirmed
                ? "Signed out."
                : "This phone is signed out. The server session could not be confirmed.",
            );
          } catch (error) {
            setMessage(
              error instanceof Error ? error.message : "Sign out failed.",
            );
          } finally {
            setBusy(false);
          }
        }}
      />
      {message ? <Text accessibilityRole="alert">{message}</Text> : null}
    </Screen>
  );
}
