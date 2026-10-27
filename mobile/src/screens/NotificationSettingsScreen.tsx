import { useTx } from "../i18n/useTx";
import { useState } from "react";
import { Platform } from "react-native";

import { version } from "../../package.json";
import { ApiError } from "../api/errors";
import { useMemberApi, useOnline } from "../app/providers";
import { randomUuidV4 } from "../booking/idempotency";
import { createSecureStore } from "../auth/secureStore";
import {
  buildDeviceRegistration,
  buildDeviceUpdate,
} from "../notifications/device";
import { currentPlatform, readPushToken } from "../notifications/push";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { NestedScreen } from "../ui/NestedScreen";
import { Text } from "../ui/Text";
import { colors } from "../ui/theme";

const store = createSecureStore();

export function NotificationSettingsScreen() {
  const tx = useTx();
  const api = useMemberApi();
  const online = useOnline();
  const [consent, setConsent] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function installationId(): Promise<string> {
    const existing = await store.getInstallationId();
    if (existing) {
      return existing;
    }
    const created = randomUuidV4();
    await store.setInstallationId(created);
    return created;
  }

  async function save(nextConsent: boolean) {
    setBusy(true);
    setMessage(null);
    try {
      const push = nextConsent
        ? await readPushToken()
        : { permission: "denied" as const, token: null };
      if (nextConsent && !push.token) {
        setConsent(false);
        setMessage(
          push.permission === "denied"
            ? "Notification permission is off. The inbox still works without push."
            : "Push is not configured on this build. The inbox still works.",
        );
        return;
      }
      const id = await installationId();
      const deviceId = await store.getDeviceId();
      const draft = {
        platform: currentPlatform(),
        installationId: id,
        pushConsent: nextConsent,
        pushToken: nextConsent ? push.token : null,
        appVersion: version,
        osVersion: String(Platform.Version),
      };
      const device = deviceId
        ? await api.updateDevice(deviceId, buildDeviceUpdate(draft))
        : await api.registerDevice(buildDeviceRegistration(draft));
      await store.setDeviceId(device.id);
      setConsent(device.push_consent);
      setMessage(
        device.push_consent
          ? "Push is on for this phone."
          : "Push is off for this phone.",
      );
    } catch (error) {
      setMessage(
        error instanceof ApiError
          ? error.message
          : error instanceof Error
            ? error.message
            : "The device could not be updated.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <NestedScreen title={tx("Notifications")}>
      <Card>
        <Text style={{ color: colors.muted }}>
          {tx(
            "The inbox does not need push permission. Push is optional and can be turned off without leaving the gym.",
          )}
        </Text>
        {!online ? (
          <Text accessibilityRole="alert">
            {tx("You are offline, so this phone cannot be updated.")}
          </Text>
        ) : null}
        <Button
          disabled={busy || !online}
          hint="Updates notification consent for this installation"
          label={consent ? "Turn push off" : "Turn push on"}
          onPress={() => save(!consent)}
        />
      </Card>
      {message ? <Text accessibilityRole="alert">{message}</Text> : null}
    </NestedScreen>
  );
}
