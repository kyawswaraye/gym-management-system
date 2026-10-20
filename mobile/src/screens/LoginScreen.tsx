import { useEffect, useState } from "react";
import { z } from "zod";
import { useForm, Controller } from "react-hook-form";

import { ApiError, fieldMessage } from "../api/errors";
import { useAuth, useOnline } from "../app/providers";
import { zodFormResolver } from "../forms/zodResolver";
import { setScreenSecure } from "../native/screenSecurity";
import { Button } from "../ui/Button";
import { Screen } from "../ui/Screen";
import { ErrorState, OfflineBanner } from "../ui/StateView";
import { Text } from "../ui/Text";
import { TextField } from "../ui/TextField";

const loginSchema = z.object({
  email: z.email("Enter the email you use to sign in."),
  password: z.string().min(1, "Enter your password."),
});

type LoginValues = z.infer<typeof loginSchema>;

export function LoginScreen() {
  const auth = useAuth();
  const online = useOnline();
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<LoginValues>({
    resolver: zodFormResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  });

  useEffect(() => {
    setScreenSecure(true);
    return () => setScreenSecure(false);
  }, []);

  return (
    <Screen title="Sign in">
      <OfflineBanner visible={!online} />
      <Text>Use the email and password for your linked gym membership.</Text>
      <Controller
        control={form.control}
        name="email"
        render={({ field, fieldState }) => (
          <TextField
            label="Email"
            hint="The email address for your member login"
            autoCapitalize="none"
            autoComplete="email"
            autoCorrect={false}
            keyboardType="email-address"
            textContentType="username"
            value={field.value}
            onChangeText={field.onChange}
            error={fieldState.error?.message}
          />
        )}
      />
      <Controller
        control={form.control}
        name="password"
        render={({ field, fieldState }) => (
          <TextField
            label="Password"
            hint="Your member password"
            secureTextEntry
            autoComplete="password"
            textContentType="password"
            value={field.value}
            onChangeText={field.onChange}
            error={fieldState.error?.message}
          />
        )}
      />
      {formError ? <ErrorState error={new Error(formError)} /> : null}
      <Button
        label="Sign in"
        hint="Signs in to your gym membership"
        disabled={form.formState.isSubmitting || !online}
        onPress={() => {
          void form.handleSubmit(async (values) => {
            setFormError(null);
            try {
              await auth.login(values.email, values.password);
            } catch (error) {
              if (error instanceof ApiError) {
                const emailError = fieldMessage(error.fields, "email");
                const passwordError = fieldMessage(error.fields, "password");
                if (emailError) {
                  form.setError("email", { message: emailError });
                }
                if (passwordError) {
                  form.setError("password", { message: passwordError });
                }
                setFormError(
                  error.status === 401
                    ? "Those credentials were not accepted."
                    : error.message,
                );
                return;
              }
              setFormError(
                error instanceof Error ? error.message : "Sign in failed.",
              );
            }
          })();
        }}
      />
    </Screen>
  );
}
