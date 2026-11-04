import { useLocale, useTx } from "../i18n/useTx";
import { useEffect, useState } from "react";
import { Dimensions, ScrollView, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { ApiError, NetworkError } from "../api/errors";
import { GoogleSignInCancelled, requestGoogleIdToken } from "../auth/google";
import { LanguageSwitch } from "../i18n/LanguageSwitch";
import { useAuth, useOnline, useSetLocale } from "../app/providers";
import { setScreenSecure } from "../native/screenSecurity";
import { BrandMark } from "../ui/BrandMark";
import { Button } from "../ui/Button";
import { PhotoBand } from "../ui/PhotoBand";
import { ErrorState, OfflineBanner } from "../ui/StateView";
import { Text } from "../ui/Text";
import { colors, space } from "../ui/theme";

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

const heroHeight = Math.round(Dimensions.get("window").height * 0.42);

export function LoginScreen() {
  const tx = useTx();
  const locale = useLocale();
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
    <SafeAreaView
      edges={["left", "right", "bottom"]}
      style={{ flex: 1, backgroundColor: colors.surface }}
    >
      <ScrollView
        bounces={false}
        contentContainerStyle={{ flexGrow: 1 }}
        keyboardShouldPersistTaps="handled"
      >
        <PhotoBand
          accessibilityLabel={tx("Gym studio")}
          animate
          height={heroHeight}
          source={require("../assets/studio-hero.jpg")}
          scrim="bottom"
        >
          <View style={{ gap: 10 }}>
            <BrandMark size={72} />
            <Text
              accessibilityRole="header"
              style={{
                color: colors.onPrimary,
                fontSize: 32,
                fontWeight: "800",
                letterSpacing: locale === "mm" ? 0 : -0.4,
                lineHeight: locale === "mm" ? 44 : 40,
              }}
            >
              {tx("GymPilot MM")}
            </Text>
            <View
              style={{
                backgroundColor: colors.accent,
                borderRadius: 2,
                height: 3,
                width: 40,
              }}
            />
          </View>
        </PhotoBand>
        <View
          style={{
            backgroundColor: colors.surface,
            flexGrow: 1,
            gap: space.md,
            marginTop: -18,
            paddingBottom: space.lg,
            paddingHorizontal: space.lg,
            paddingTop: space.lg,
          }}
        >
          <View style={{ alignItems: "flex-end" }}>
            <LanguageSwitch onChange={(value) => void setLocale(value)} />
          </View>
          <Text
            style={{
              color: colors.muted,
              fontSize: 16,
              lineHeight: 24,
              textAlign: "left",
            }}
          >
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
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}
