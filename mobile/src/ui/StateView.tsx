import { ActivityIndicator, View } from "react-native";

import { useReducedMotion } from "../a11y/motion";
import { safeDiagnostic } from "../logging/redact";
import { colors, space } from "./theme";
import { Button } from "./Button";
import { Text } from "./Text";

export function LoadingState({ label = "Loading" }: { label?: string }) {
  const reduced = useReducedMotion();
  return (
    <View
      accessibilityRole="progressbar"
      accessibilityLabel={label}
      style={{ gap: space.sm }}
    >
      {reduced ? null : <ActivityIndicator color={colors.primary} />}
      <Text>{label}</Text>
    </View>
  );
}

export function EmptyState({ message }: { message: string }) {
  return <Text accessibilityLabel={message}>{message}</Text>;
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
  const message = offline
    ? "You are offline. Reconnect, then try again."
    : safeDiagnostic(error);
  return (
    <View
      accessibilityRole="alert"
      style={{
        gap: space.sm,
        backgroundColor: colors.dangerSurface,
        padding: space.md,
        borderRadius: 8,
      }}
    >
      <Text style={{ color: colors.danger }}>{message}</Text>
      {onRetry ? (
        <Button
          label="Try again"
          hint="Repeats the request"
          onPress={onRetry}
        />
      ) : null}
    </View>
  );
}

export function OfflineBanner({ visible }: { visible: boolean }) {
  if (!visible) {
    return null;
  }
  return (
    <View
      accessibilityRole="alert"
      style={{ backgroundColor: colors.infoSurface, padding: space.sm }}
    >
      <Text>You are offline. Pull to refresh when the connection returns.</Text>
    </View>
  );
}
