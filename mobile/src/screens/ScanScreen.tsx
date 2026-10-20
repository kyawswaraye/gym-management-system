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
import { Screen } from "../ui/Screen";
import { Text } from "../ui/Text";
import { colors } from "../ui/theme";

export function ScanScreen() {
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
          ? "Point the camera at the gym check-in code."
          : "That code is not a gym check-in code.",
      );
      return;
    }
    inFlight.current = true;
    setScanning(true);
    try {
      const visit = await api.scan(parsed.payload);
      setResult(`Checked in at ${visit.check_in_at}.`);
    } catch (error) {
      if (error instanceof ApiError) {
        setResult(scanErrorMessage(error.code, error.message));
      } else {
        setResult(error instanceof Error ? error.message : "Check-in failed.");
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
    <Screen title="Check in">
      <Text>
        Scan the code on the gym screen. If the camera is unavailable, the front
        desk can check you in.
      </Text>
      {!online ? (
        <Text accessibilityRole="alert">
          You are offline, so a scan cannot be sent.
        </Text>
      ) : null}
      {!hasPermission ? (
        <>
          <Text>
            Camera access is off. You can still check in with a staff member at
            the front desk.
          </Text>
          {canRequestPermission ? (
            <Button
              label="Allow camera"
              hint="Asks the phone for camera access"
              onPress={() => requestPermission()}
            />
          ) : (
            <Text>
              Turn camera access on in system settings, then return here.
            </Text>
          )}
        </>
      ) : null}
      {hasPermission && device ? (
        <View accessibilityLabel="Check-in camera" style={styles.preview}>
          <Camera
            style={StyleSheet.absoluteFill}
            device={device}
            isActive={online && !scanning}
            outputs={[objectOutput]}
          />
        </View>
      ) : null}
      {hasPermission && !device ? (
        <Text>No back camera is available on this phone.</Text>
      ) : null}
      {result ? (
        <Text accessibilityRole="alert" accessibilityLiveRegion="polite">
          {result}
        </Text>
      ) : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  preview: {
    height: 280,
    borderRadius: 8,
    overflow: "hidden",
    backgroundColor: colors.text,
  },
});
