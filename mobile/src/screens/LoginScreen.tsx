import { useTx } from "../i18n/useTx";
import { useEffect, useState } from "react";
import { ScrollView, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { ApiError, NetworkError } from "../api/errors";
import { GoogleSignInCancelled, requestGoogleIdToken } from "../auth/google";
import { LanguageSwitch } from "../i18n/LanguageSwitch";
import { useAuth, useOnline, useSetLocale } from "../app/providers";
import { setScreenSecure } from "../native/screenSecurity";
import { BrandMark } from "../ui/BrandMark";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { ErrorState, OfflineBanner } from "../ui/StateView";
import { Text } from "../ui/Text";
import { colors, elevation, space } from "../ui/theme";

const GOOGLE_ERRORS: Record<string, string> = {
  MEMBER_NOT_FOUND: "No member account uses this Google email.",
  MEMBER_NOT_ACTIVE: "This member account is not active.",
  MEMBER_SIGN_IN_AMBIGUOUS:
    "This Google email matches more than one active member.",
  MEMBER_ALREADY_LINKED:
    "This member account is already linked to a different login.",
  GOOGLE_ACCOUNT_ALREADY_LINKED:
    "This Google account is already linked to another member.",
  GOOGLE_SIGN_IN_NOT_CONFIGURED: "Google sign-in is not configured.",
  MEMBER_SIGN_IN_UNAVAILABLE:
    "This Google account cannot sign in to the member app.",
};

export function LoginScreen() {
  const tx = useTx();
  const setLocale = useSetLocale();
  const auth = useAuth();
  const online = useOnline();
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    setScreenSecure(true);
    return () => setScreenSecure(false);
  }, []);

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }}>
      <ScrollView
        contentContainerStyle={{
          flexGrow: 1,
          gap: space.md,
          paddingBottom: space.lg,
          paddingHorizontal: space.lg,
          paddingTop: space.md,
        }}
      >
        <LanguageSwitch onChange={(value) => void setLocale(value)} />
        <View
          style={{
            alignItems: "center",
            flexGrow: 1,
            gap: space.md,
            justifyContent: "center",
          }}
        >
          <View
            style={{
              alignItems: "center",
              backgroundColor: colors.surface,
              borderColor: colors.border,
              borderRadius: 32,
              borderWidth: 1,
              gap: 10,
              paddingHorizontal: 28,
              paddingVertical: 22,
              ...elevation,
            }}
          >
            <BrandMark size={88} />
            <Text
              accessibilityRole="header"
              style={{ fontSize: 22, fontWeight: "700", letterSpacing: 0.3 }}
            >
              {tx("GymPilot MM")}
            </Text>
            <View
              style={{
                backgroundColor: colors.accent,
                borderRadius: 2,
                height: 3,
                width: 32,
              }}
            />
          </View>
        </View>
        <Card>
          <Text style={{ color: colors.muted, textAlign: "center" }}>
            {tx("Sign in with the Google account that uses your member email.")}
          </Text>
          <OfflineBanner visible={!online} />
          {formError ? <ErrorState error={new Error(formError)} /> : null}
          <Button
            label={tx("Continue with Google")}
            hint={tx("Signs in to your gym membership")}
            disabled={submitting}
            onPress={() => {
              void (async () => {
                setFormError(null);
                setSubmitting(true);
                try {
                  const idToken = await requestGoogleIdToken();
                  await auth.login(idToken);
                } catch (error) {
                  if (error instanceof GoogleSignInCancelled) {
                    setFormError(tx("Google sign-in was cancelled."));
                    return;
                  }
                  if (error instanceof NetworkError) {
                    setFormError(
                      tx(
                        online
                          ? "The gym server could not be reached. Check the connection and try again."
                          : "You appear to be offline. Check the connection and try again.",
                      ),
                    );
                    return;
                  }
                  if (error instanceof ApiError) {
                    const known = GOOGLE_ERRORS[error.code];
                    setFormError(
                      known
                        ? tx(known)
                        : error.status === 401
                          ? tx("Those credentials were not accepted.")
                          : error.message,
                    );
                    return;
                  }
                  setFormError(
                    error instanceof Error
                      ? tx(error.message)
                      : tx("Sign in failed."),
                  );
                } finally {
                  setSubmitting(false);
                }
              })();
            }}
          />
        </Card>
      </ScrollView>
    </SafeAreaView>
  );
}
