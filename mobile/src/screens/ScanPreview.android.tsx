import { useBarcodeScannerOutput } from "react-native-vision-camera-barcode-scanner";

import { ScanCamera, type ScanPreviewProps } from "./scanCamera";

const QR_FORMATS = ["qr-code"] as ["qr-code"];

export function ScanPreview({ active, onCode }: ScanPreviewProps) {
  const barcodeOutput = useBarcodeScannerOutput({
    barcodeFormats: QR_FORMATS,
    onBarcodeScanned: (barcodes) => {
      for (const barcode of barcodes) {
        if (barcode.rawValue) {
          onCode(barcode.rawValue);
          return;
        }
      }
    },
    onError: () => undefined,
  });
  return <ScanCamera active={active} output={barcodeOutput} />;
}
