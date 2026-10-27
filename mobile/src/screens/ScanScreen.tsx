import { useTx } from "../i18n/useTx";
import { useRef, useState } from "react";
import { StyleSheet, View } from "react-native";
import {
  Camera,
  isScannedCode,
  useCameraDevice,
  useCameraPermission,
  useObjectOutput,
} from "react-native-vision-camera";

import { ApiError } from "../api/errors";
import { useMemberApi, useOnline } from "../app/providers";
import { readCheckInPayload } from "../scan/payload";
import { scanErrorMessage } from "../scan/errors";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Screen } from "../ui/Screen";
import { Text } from "../ui/Text";
import { colors, radius } from "../ui/theme";

export function ScanScreen() {
  const tx = useTx();
  const api = useMemberApi();
  const online = useOnline();
  const { hasPermission, requestPermission, canRequestPermission } =
    useCameraPermission();
  const device = useCameraDevice("back");
  const [result, setResult] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const inFlight = useRef(false);

  async function submit(raw: string) {
    if (inFlight.current) {
      return;
    }
    const parsed = readCheckInPayload(raw);
    if (!parsed.ok) {
      setResult(
        parsed.reason === "empty"
          ? tx("Point the camera at the gym check-in code.")
          : tx("That code is not a gym check-in code."),
      );
      return;
    }
    inFlight.current = true;
    setScanning(true);
    try {
      const visit = await api.scan(parsed.payload);
      setResult(tx("Checked in at {time}.", { time: visit.check_in_at }));
    } catch (error) {
      if (error instanceof ApiError) {
        setResult(tx(scanErrorMessage(error.code, error.message)));
      } else {
        setResult(
          error instanceof Error ? tx(error.message) : tx("Check-in failed."),
        );
      }
    } finally {
      inFlight.current = false;
      setScanning(false);
    }
  }

  const objectOutput = useObjectOutput({
    types: ["qr"],
    onObjectsScanned: (objects) => {
      for (const object of objects) {
        if (isScannedCode(object) && object.value) {
          void submit(object.value);
          return;
        }
      }
    },
  });

  return (
    <Screen title={tx("Check in")}>
      <Text style={{ color: colors.muted }}>
        {tx(
          "Scan the code on the gym screen. If the camera is unavailable, the front desk can check you in.",
        )}
      </Text>
      {!online ? (
        <Text accessibilityRole="alert" style={{ color: colors.muted }}>
          {tx("You are offline, so a scan cannot be sent.")}
        </Text>
      ) : null}
      {!hasPermission ? (
        <>
          <Text style={{ color: colors.muted }}>
            {tx(
              "Camera access is off. You can still check in with a staff member at the front desk.",
            )}
          </Text>
          {canRequestPermission ? (
            <Button
              label={tx("Allow camera")}
              hint={tx("Asks the phone for camera access")}
              onPress={() => requestPermission()}
            />
          ) : (
            <Text style={{ color: colors.muted }}>
              {tx(
                "Turn camera access on in system settings, then return here.",
              )}
            </Text>
          )}
        </>
      ) : null}
      {hasPermission && device ? (
        <View accessibilityLabel={tx("Check-in camera")} style={styles.preview}>
          <Camera
            style={StyleSheet.absoluteFill}
            device={device}
            isActive={online && !scanning}
            outputs={[objectOutput]}
          />
        </View>
      ) : null}
      {hasPermission && !device ? (
        <Text>{tx("No back camera is available on this phone.")}</Text>
      ) : null}
      {result ? (
        <Card>
          <Text accessibilityLiveRegion="polite" accessibilityRole="alert">
            {result}
          </Text>
        </Card>
      ) : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  preview: {
    height: 320,
    borderRadius: radius.md,
    overflow: "hidden",
    backgroundColor: colors.text,
  },
});
