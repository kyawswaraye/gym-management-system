import { env, type AppEnv } from "../config/env";

type GoogleSignInModule = {
  GoogleSignin: {
    configure(options: { webClientId: string; iosClientId?: string }): void;
    hasPlayServices(options?: {
      showPlayServicesUpdateDialog?: boolean;
    }): Promise<boolean>;
    signIn(): Promise<
      | { type: "success"; data: { idToken: string | null } }
      | { type: "cancelled"; data: null }
    >;
  };
  statusCodes: { SIGN_IN_CANCELLED: string };
};

export class GoogleSignInCancelled extends Error {
  constructor() {
    super("Google sign-in was cancelled.");
    this.name = "GoogleSignInCancelled";
  }
}

export async function requestGoogleIdToken(
  config: AppEnv = env,
): Promise<string> {
  if (!config.googleWebClientId) {
    throw new Error("Google sign-in is not configured.");
  }
  const loaded = (await import(
    "@react-native-google-signin/google-signin"
  )) as GoogleSignInModule;
  loaded.GoogleSignin.configure({
    webClientId: config.googleWebClientId,
    iosClientId: config.googleIosClientId || undefined,
  });
  await loaded.GoogleSignin.hasPlayServices({
    showPlayServicesUpdateDialog: true,
  });
  try {
    const response = await loaded.GoogleSignin.signIn();
    if (response.type === "cancelled") {
      throw new GoogleSignInCancelled();
    }
    const idToken = response.data.idToken;
    if (!idToken) {
      throw new Error("Google did not return a sign-in token.");
    }
    return idToken;
  } catch (error) {
    if (error instanceof GoogleSignInCancelled) {
      throw error;
    }
    if (isCancelled(error, loaded.statusCodes.SIGN_IN_CANCELLED)) {
      throw new GoogleSignInCancelled();
    }
    throw error;
  }
}

function isCancelled(error: unknown, cancelledCode: string): boolean {
  if (typeof error !== "object" || error === null || !("code" in error)) {
    return false;
  }
  const code = String(error.code);
  return code === cancelledCode || code === "SIGN_IN_CANCELLED" || code === "-5";
}
