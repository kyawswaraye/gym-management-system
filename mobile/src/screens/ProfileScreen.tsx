import { useTx } from "../i18n/useTx";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Controller, useForm } from "react-hook-form";
import { useState } from "react";
import { Image, RefreshControl, View } from "react-native";
import { launchCamera, launchImageLibrary } from "react-native-image-picker";

import { ApiError, fieldMessage } from "../api/errors";
import { useMemberApi, useOnline } from "../app/providers";
import {
  profileFormSchema,
  profilePatch,
  profileToForm,
  type ProfileFormValues,
} from "../forms/profile";
import { zodFormResolver } from "../forms/zodResolver";
import {
  postPresignedPhoto,
  uploadProfilePhoto,
  type SelectedPhoto,
} from "../files/profilePhoto";
import { queryKeys } from "../query/keys";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { NestedScreen } from "../ui/NestedScreen";
import { ErrorState, LoadingState, OfflineBanner } from "../ui/StateView";
import { Text } from "../ui/Text";
import { TextField } from "../ui/TextField";
import { colors } from "../ui/theme";

const genders = ["FEMALE", "MALE", "OTHER", "UNSPECIFIED"] as const;

export function ProfileScreen() {
  const tx = useTx();
  const api = useMemberApi();
  const online = useOnline();
  const queryClient = useQueryClient();
  const profile = useQuery({ queryKey: queryKeys.me, queryFn: () => api.me() });
  const photoId = profile.data?.profile_photo_asset_id ?? null;
  const photo = useQuery({
    queryKey: queryKeys.photo(photoId ?? "none"),
    enabled: Boolean(photoId),
    queryFn: () => api.photoDownloadUrl(photoId ?? ""),
  });
  const [message, setMessage] = useState<string | null>(null);
  const form = useForm<ProfileFormValues>({
    resolver: zodFormResolver(profileFormSchema),
    values: profile.data ? profileToForm(profile.data) : undefined,
    defaultValues: profile.data ? profileToForm(profile.data) : emptyProfile(),
  });

  async function save(values: ProfileFormValues) {
    if (!profile.data) {
      return;
    }
    const patch = profilePatch(profileToForm(profile.data), values);
    if (!patch) {
      setMessage(tx("Change at least one field."));
      return;
    }
    setMessage(null);
    try {
      await api.updateMe(patch);
      await queryClient.invalidateQueries({ queryKey: queryKeys.me });
      setMessage(tx("Profile saved."));
    } catch (error) {
      if (error instanceof ApiError) {
        for (const [field, messages] of Object.entries(error.fields)) {
          const text = messages[0];
          if (text && field in values) {
            form.setError(field as keyof ProfileFormValues, { message: text });
          }
        }
        setMessage(
          fieldMessage(error.fields, "non_field_errors") ?? error.message,
        );
        return;
      }
      setMessage(
        error instanceof Error
          ? error.message
          : "The profile could not be saved.",
      );
    }
  }

  async function choosePhoto(source: "library" | "camera") {
    const picker = source === "library" ? launchImageLibrary : launchCamera;
    const selected = await picker({
      mediaType: "photo",
      includeBase64: true,
      selectionLimit: 1,
    });
    const asset = selected.assets?.[0];
    if (!asset?.uri || !asset.base64 || !asset.type) {
      if (selected.errorCode) {
        setMessage(tx("The photo could not be opened."));
      }
      return;
    }
    const photoFile: SelectedPhoto = {
      uri: asset.uri,
      fileName: asset.fileName ?? "profile",
      contentType: asset.type,
      base64: asset.base64,
    };
    setMessage(null);
    try {
      const uploaded = await uploadProfilePhoto(
        api,
        photoFile,
        postPresignedPhoto,
      );
      setMessage(
        uploaded.attachable
          ? "Profile photo updated."
          : "The photo is being checked. Try again in a moment.",
      );
      await queryClient.invalidateQueries({ queryKey: queryKeys.me });
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "The photo could not be uploaded.",
      );
    }
  }

  return (
    <NestedScreen
      title={tx("Profile")}
      refreshControl={
        <RefreshControl
          refreshing={profile.isRefetching}
          onRefresh={() => {
            void profile.refetch();
          }}
        />
      }
    >
      <OfflineBanner visible={!online} />
      {profile.isLoading ? (
        <LoadingState label={tx("Loading profile")} />
      ) : null}
      {profile.isError ? (
        <ErrorState
          error={profile.error}
          offline={!online}
          onRetry={() => profile.refetch()}
        />
      ) : null}
      {profile.data ? (
        <>
          <Card>
            <View style={{ alignItems: "center", gap: 8 }}>
              {photo.data?.url ? (
                <Image
                  accessibilityLabel={tx("Profile photo")}
                  source={{ uri: photo.data.url }}
                  style={{
                    borderColor: colors.border,
                    borderRadius: 48,
                    borderWidth: 1,
                    height: 96,
                    width: 96,
                  }}
                />
              ) : (
                <Text style={{ color: colors.muted }}>
                  {tx("No profile photo yet.")}
                </Text>
              )}
              <Text style={{ fontSize: 20, fontWeight: "700" }}>
                {profile.data.first_name} {profile.data.last_name}
              </Text>
              <Text style={{ color: colors.muted }}>
                {profile.data.member_number}
              </Text>
            </View>
            <Button
              hint="Opens the system photo picker"
              label="Choose photo"
              variant="secondary"
              onPress={() => choosePhoto("library")}
            />
            <Button
              hint="Uses the camera for a profile photo"
              label="Take photo"
              variant="secondary"
              onPress={() => choosePhoto("camera")}
            />
            {photoId ? (
              <Button
                hint="Deletes your profile photo"
                label="Remove photo"
                variant="danger"
                onPress={async () => {
                  await api.deletePhoto(photoId);
                  await queryClient.invalidateQueries({
                    queryKey: queryKeys.me,
                  });
                  setMessage(tx("Profile photo removed."));
                }}
              />
            ) : null}
          </Card>
          <Card>
            <Text style={{ fontWeight: "700" }}>{tx("Gender")}</Text>
            <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
              {genders.map((gender) => (
                <Button
                  key={gender}
                  label={gender}
                  style={{ flexGrow: 1 }}
                  variant={
                    form.watch("gender") === gender ? "primary" : "secondary"
                  }
                  onPress={() =>
                    form.setValue("gender", gender, { shouldDirty: true })
                  }
                />
              ))}
            </View>
          </Card>
          <Card>
            <Field control={form.control} name="phone" label={tx("Phone")} />
            <Field
              control={form.control}
              name="email"
              label={tx("Contact email")}
              keyboardType="email-address"
            />
            <Field
              control={form.control}
              name="date_of_birth"
              label={tx("Date of birth")}
              hint={tx("YYYY-MM-DD")}
            />
            <Field
              control={form.control}
              name="address_line1"
              label={tx("Address line 1")}
            />
            <Field
              control={form.control}
              name="address_line2"
              label={tx("Address line 2")}
            />
            <Field control={form.control} name="city" label={tx("City")} />
            <Field control={form.control} name="region" label={tx("Region")} />
            <Field
              control={form.control}
              name="postal_code"
              label={tx("Postal code")}
            />
            <Field
              control={form.control}
              name="country_code"
              label={tx("Country code")}
            />
            <Field
              control={form.control}
              name="emergency_contact_name"
              label={tx("Emergency contact name")}
            />
            <Field
              control={form.control}
              name="emergency_contact_phone"
              label={tx("Emergency contact phone")}
            />
          </Card>
          {message ? <Text accessibilityRole="alert">{message}</Text> : null}
          <Button
            label={tx("Save profile")}
            hint={tx("Saves the contact fields you changed")}
            disabled={form.formState.isSubmitting || !online}
            onPress={() => {
              void form.handleSubmit(save)();
            }}
          />
        </>
      ) : null}
    </NestedScreen>
  );
}

function Field({
  control,
  name,
  label,
  hint,
  keyboardType,
}: {
  control: ReturnType<typeof useForm<ProfileFormValues>>["control"];
  name: keyof ProfileFormValues;
  label: string;
  hint?: string;
  keyboardType?: "email-address" | "default";
}) {
  return (
    <Controller
      control={control}
      name={name}
      render={({ field, fieldState }) => (
        <TextField
          label={label}
          hint={hint}
          keyboardType={keyboardType}
          autoCapitalize={
            keyboardType === "email-address" ? "none" : "sentences"
          }
          value={field.value}
          onChangeText={field.onChange}
          error={fieldState.error?.message}
        />
      )}
    />
  );
}

function emptyProfile(): ProfileFormValues {
  return {
    phone: "",
    email: "",
    gender: "",
    date_of_birth: "",
    address_line1: "",
    address_line2: "",
    city: "",
    region: "",
    postal_code: "",
    country_code: "",
    emergency_contact_name: "",
    emergency_contact_phone: "",
  };
}
