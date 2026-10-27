import { useTx } from "../i18n/useTx";
import { ActivityIndicator, View } from "react-native";

import { useReducedMotion } from "../a11y/motion";
import { safeDiagnostic } from "../logging/redact";
import { colors, radius, space } from "./theme";
import { Button } from "./Button";
import { Text } from "./Text";

export function LoadingState({ label = "Loading" }: { label?: string }) {
  const tx = useTx();
  const reduced = useReducedMotion();
  const text = tx(label);
  return (
    <View
      accessibilityRole="progressbar"
      accessibilityLabel={text}
      style={{ gap: space.sm }}
    >
      {reduced ? null : <ActivityIndicator color={colors.primary} />}
      <Text style={{ color: colors.muted }}>{text}</Text>
    </View>
  );
}

export function EmptyState({ message }: { message: string }) {
  const tx = useTx();
  const text = tx(message);
  return (
    <Text accessibilityLabel={text} style={{ color: colors.muted }}>
      {text}
    </Text>
  );
}

export function ErrorState({
  error,
  onRetry,
  offline,
}: {
  error: unknown;
  onRetry?: () => void;
  offline?: boolean;
}) {
  const tx = useTx();
  const message = offline
    ? tx("You are offline. Reconnect, then try again.")
    : tx(safeDiagnostic(error));
  return (
    <View
      accessibilityRole="alert"
      style={{
        gap: space.sm,
        backgroundColor: colors.dangerSurface,
        padding: space.md,
        borderRadius: radius.md,
      }}
    >
      <Text style={{ color: colors.danger }}>{message}</Text>
      {onRetry ? (
        <Button
          label={tx("Try again")}
          hint={tx("Repeats the request")}
          onPress={onRetry}
        />
      ) : null}
    </View>
  );
}

export function OfflineBanner({ visible }: { visible: boolean }) {
  const tx = useTx();
  if (!visible) {
    return null;
  }
  return (
    <View
      accessibilityRole="alert"
      style={{
        backgroundColor: colors.infoSurface,
        borderLeftColor: colors.primary,
        borderLeftWidth: 3,
        borderRadius: radius.sm,
        padding: space.md,
      }}
    >
      <Text>
        {tx("You are offline. Pull to refresh when the connection returns.")}
      </Text>
    </View>
  );
}
