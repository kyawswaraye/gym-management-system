import { useEffect, useRef, useState } from "react";
import { Platform, Pressable, StyleSheet, View } from "react-native";
import { version } from "../../package.json";
import { useMemberApi } from "../app/providers";
import { createSecureStore } from "../auth/secureStore";
import { openInbox } from "../navigation/inboxLink";
import { Text } from "../ui/Text";
import { colors, elevation, space } from "../ui/theme";
import {
  noticeFromRemoteMessage,
  rememberNotice,
  rotatedTokenUpdate,
  type RemoteNotice,
} from "./messages";

const store = createSecureStore();

type MessagingInstance = {
  onMessage?(listener: (message: unknown) => void): () => void;
  onNotificationOpenedApp?(listener: (message: unknown) => void): () => void;
  getInitialNotification?(): Promise<unknown>;
  onTokenRefresh?(listener: (token: string) => void): () => void;
};

type MessagingModule = {
  (): MessagingInstance;
};

export function PushBridge() {
  const api = useMemberApi();
  const seen = useRef(new Set<string>());
  const [banner, setBanner] = useState<RemoteNotice | null>(null);

  useEffect(() => {
    let messaging: MessagingModule;
    try {
      messaging = require("@react-native-firebase/messaging")
        .default as MessagingModule;
    } catch {
      return undefined;
    }
    const instance = messaging();
    let active = true;

    function openNotice(message: unknown): void {
      const notice = noticeFromRemoteMessage(
        message as Parameters<typeof noticeFromRemoteMessage>[0],
      );
      if (!notice) {
        openInbox(null);
        return;
      }
      rememberNotice(seen.current, notice);
      openInbox(notice.notificationId);
    }

    const unsubscribeMessage = instance.onMessage?.(async (message) => {
      if (!(await store.getPushConsent())) {
        return;
      }
      const notice = noticeFromRemoteMessage(
        message as Parameters<typeof noticeFromRemoteMessage>[0],
      );
      if (!notice || !rememberNotice(seen.current, notice)) {
        return;
      }
      if (active) {
        setBanner(notice);
      }
    });
    const unsubscribeOpened = instance.onNotificationOpenedApp?.(openNotice);
    void instance.getInitialNotification?.().then((message) => {
      if (active && message) {
        openNotice(message);
      }
    });
    const unsubscribeToken = instance.onTokenRefresh?.((token) => {
      void (async () => {
        const update = rotatedTokenUpdate({
          signedIn: true,
          pushConsent: await store.getPushConsent(),
          deviceId: await store.getDeviceId(),
          token,
          appVersion: version,
          osVersion: String(Platform.Version),
        });
        if (!update) {
          return;
        }
        try {
          await api.updateDevice(update.deviceId, update.body);
        } catch {
          // The saved installation stays. Notification settings can retry.
        }
      })();
    });
    return () => {
      active = false;
      unsubscribeMessage?.();
      unsubscribeOpened?.();
      unsubscribeToken?.();
    };
  }, [api]);

  if (!banner) {
    return null;
  }
  return (
    <View pointerEvents="box-none" style={styles.host}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={banner.title}
        onPress={() => {
          openInbox(banner.notificationId);
          setBanner(null);
        }}
        style={styles.banner}
      >
        <Text style={styles.title}>{banner.title}</Text>
        {banner.body ? <Text>{banner.body}</Text> : null}
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  host: {
    left: space.md,
    position: "absolute",
    right: space.md,
    top: space.md,
    zIndex: 20,
  },
  banner: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 12,
    borderWidth: 1,
    gap: space.xs,
    padding: space.md,
    ...elevation,
  },
  title: {
    fontWeight: "700",
  },
});
