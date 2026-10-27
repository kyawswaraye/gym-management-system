import { useEffect, useState, type JSX, type ReactNode } from "react";
import { View } from "react-native";
import {
  Images,
  NitroImage,
  type Image as LoadedImage,
} from "react-native-nitro-image";

import type { GymLogoMedia } from "../api/memberApi";
import { colors } from "./theme";

type LogoViewProps = {
  image: LoadedImage;
  resizeMode?: "contain";
  style?: { height: number; width: number };
};

const LogoView = NitroImage as (props: LogoViewProps) => JSX.Element;

function encodedBuffer(bytes: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(bytes.byteLength);
  copy.set(bytes);
  return copy.buffer;
}

export function GymLogoImage({
  media,
  label,
  fallback,
  size = 64,
}: {
  media: GymLogoMedia;
  label: string;
  fallback: ReactNode;
  size?: number;
}) {
  const [image, setImage] = useState<LoadedImage | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    setImage(null);
    setFailed(false);
    const imageFormat = media.contentType === "image/jpeg" ? "jpg" : "png";
    try {
      void Images.loadFromEncodedImageDataAsync({
        buffer: encodedBuffer(media.bytes),
        height: 1,
        imageFormat,
        width: 1,
      })
        .then((loaded) => {
          if (active) {
            setImage(loaded);
          }
        })
        .catch(() => {
          if (active) {
            setFailed(true);
          }
        });
    } catch {
      if (active) {
        setFailed(true);
      }
    }
    return () => {
      active = false;
    };
  }, [media]);

  if (failed) {
    return <>{fallback}</>;
  }
  const frame = { height: size, width: size };
  if (!image) {
    return (
      <View
        style={{
          backgroundColor: colors.primarySoft,
          borderRadius: 16,
          height: size,
          width: size,
        }}
      />
    );
  }
  return (
    <View accessibilityLabel={label} accessibilityRole="image" style={frame}>
      <LogoView image={image} resizeMode="contain" style={frame} />
    </View>
  );
}
