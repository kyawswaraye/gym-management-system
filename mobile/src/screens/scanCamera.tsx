import { StyleSheet, View } from "react-native";
import { Camera, useCameraDevice } from "react-native-vision-camera";
import type { CameraOutput } from "react-native-vision-camera";

import { useTx } from "../i18n/useTx";
import { Text } from "../ui/Text";
import { colors, radius } from "../ui/theme";

export type ScanPreviewProps = {
  active: boolean;
  onCode: (value: string) => void;
};

export function ScanCamera({
  active,
  output,
}: {
  active: boolean;
  output: CameraOutput;
}) {
  const tx = useTx();
  const device = useCameraDevice("back");
  if (!device) {
    return <Text>{tx("No back camera is available on this phone.")}</Text>;
  }
  return (
    <View accessibilityLabel={tx("Check-in camera")} style={styles.preview}>
      <Camera
        style={StyleSheet.absoluteFill}
        device={device}
        isActive={active}
        outputs={[output]}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  preview: {
    backgroundColor: colors.text,
    borderRadius: radius.md,
    flex: 1,
    minHeight: 220,
    overflow: "hidden",
  },
});
