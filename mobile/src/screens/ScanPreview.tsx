import { isScannedCode, useObjectOutput } from "react-native-vision-camera";

import { ScanCamera, type ScanPreviewProps } from "./scanCamera";

const QR_TYPES = ["qr"] as ["qr"];

export function ScanPreview({ active, onCode }: ScanPreviewProps) {
  const objectOutput = useObjectOutput({
    types: QR_TYPES,
    onObjectsScanned: (objects) => {
      for (const object of objects) {
        if (isScannedCode(object) && object.value) {
          onCode(object.value);
          return;
        }
      }
    },
  });
  return <ScanCamera active={active} output={objectOutput} />;
}
