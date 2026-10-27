import { useLocale, useTx } from "../i18n/useTx";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Controller, useForm } from "react-hook-form";
import { useState } from "react";
import {
  Image,
  PermissionsAndroid,
  Platform,
  RefreshControl,
  View,
} from "react-native";
import { launchCamera, launchImageLibrary } from "react-native-image-picker";

import { ApiError, fieldMessage } from "../api/errors";
import { useMemberApi, useOnline } from "../app/providers";
import {
  phoneDigits,
  profileFormSchema,
  profilePatch,
  profileToForm,
  type ProfileFormValues,
} from "../forms/profile";
import {
  citiesForRegion,
  MYANMAR_REGIONS,
  type MyanmarPlace,
} from "../forms/myanmar-locations";
import { zodFormResolver } from "../forms/zodResolver";
import type { Locale } from "../i18n/locale";
import {
  photoContentType,
  postPresignedPhoto,
  readUriBytes,
  uploadProfilePhoto,
  type SelectedPhoto,
} from "../files/profilePhoto";
import { queryKeys } from "../query/keys";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { DateField } from "../ui/DateField";
import { NestedScreen } from "../ui/NestedScreen";
import { SelectField } from "../ui/SelectField";
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
  const locale = useLocale();
  const [message, setMessage] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
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
    if (uploading) {
      return;
    }
    if (source === "camera" && !(await ensureCameraPermission())) {
      setMessage(tx("Allow camera access in system settings, then try again."));
      return;
    }
    setUploading(true);
    setMessage(null);
    try {
      const picker = source === "library" ? launchImageLibrary : launchCamera;
      const selected = await picker({
        assetRepresentationMode: "compatible",
        cameraType: "front",
        includeBase64: false,
        maxHeight: 1600,
        maxWidth: 1600,
        mediaType: "photo",
        presentationStyle: "fullScreen",
        quality: 0.8,
        restrictMimeTypes: ["image/jpeg", "image/png", "image/webp"],
        saveToPhotos: false,
        selectionLimit: 1,
      });
      if (selected.didCancel) {
        return;
      }
      if (selected.errorCode || !selected.assets?.[0]?.uri) {
        setMessage(
          tx(
            selected.errorCode === "permission" ||
              selected.errorCode === "camera_unavailable"
              ? source === "camera"
                ? "Allow camera access in system settings, then try again."
                : "Allow photo access in system settings, then try again."
              : "The photo could not be opened. Choose a JPEG, PNG, or WebP photo.",
          ),
        );
        return;
      }
      const asset = selected.assets[0];
      const photoFile: SelectedPhoto = {
        uri: asset.uri ?? "",
        fileName: asset.fileName ?? "profile.jpg",
        contentType: photoContentType(asset.type, asset.fileName),
        bytes: await readUriBytes(asset.uri ?? ""),
      };
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
    } finally {
      setUploading(false);
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
              disabled={uploading}
              hint="Opens the system photo picker"
              label="Choose photo"
              variant="secondary"
              onPress={() => {
                void choosePhoto("library");
              }}
            />
            <Button
              disabled={uploading}
              hint="Uses the camera for a profile photo"
              label="Take photo"
              variant="secondary"
              onPress={() => {
                void choosePhoto("camera");
              }}
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
            <Field
              control={form.control}
              name="phone"
              label={tx("Phone")}
              keyboardType="phone-pad"
              transform={phoneDigits}
            />
            <Field
              control={form.control}
              name="email"
              label={tx("Contact email")}
              keyboardType="email-address"
            />
            <Controller
              control={form.control}
              name="date_of_birth"
              render={({ field, fieldState }) => (
                <DateField
                  error={fieldState.error?.message}
                  label={tx("Date of birth")}
                  value={field.value}
                  onChange={field.onChange}
                />
              )}
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
            <Controller
              control={form.control}
              name="region"
              render={({ field, fieldState }) => (
                <SelectField
                  error={fieldState.error?.message}
                  label={tx("Region")}
                  options={placeOptions(
                    placesWithCurrent(MYANMAR_REGIONS, field.value),
                    locale,
                  )}
                  placeholder="Choose a region"
                  value={field.value}
                  onChange={(next) => {
                    const currentCity = form.getValues("city");
                    field.onChange(next);
                    if (
                      !citiesForRegion(next).some(
                        (place) => place.value === currentCity,
                      )
                    ) {
                      form.setValue("city", "", { shouldDirty: true });
                    }
                  }}
                />
              )}
            />
            <Controller
              control={form.control}
              name="city"
              render={({ field, fieldState }) => (
                <SelectField
                  disabled={!form.watch("region")}
                  error={fieldState.error?.message}
                  label={tx("City")}
                  options={placeOptions(
                    placesWithCurrent(
                      citiesForRegion(form.watch("region")),
                      field.value,
                    ),
                    locale,
                  )}
                  placeholder={
                    form.watch("region")
                      ? "Choose a city"
                      : "Choose a region first."
                  }
                  value={field.value}
                  onChange={field.onChange}
                />
              )}
            />
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
              keyboardType="phone-pad"
              label={tx("Emergency contact phone")}
              name="emergency_contact_phone"
              transform={phoneDigits}
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
  transform,
}: {
  control: ReturnType<typeof useForm<ProfileFormValues>>["control"];
  name: keyof ProfileFormValues;
  label: string;
  hint?: string;
  keyboardType?: "email-address" | "phone-pad" | "default";
  transform?: (value: string) => string;
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
            keyboardType === "email-address" || keyboardType === "phone-pad"
              ? "none"
              : "sentences"
          }
          autoCorrect={keyboardType === "phone-pad" ? false : undefined}
          value={transform ? transform(field.value) : field.value}
          onChangeText={(text) =>
            field.onChange(transform ? transform(text) : text)
          }
          error={fieldState.error?.message}
        />
      )}
    />
  );
}

function placeLabel(place: MyanmarPlace, locale: Locale): string {
  return locale === "mm" ? place.my : place.value;
}

function placesWithCurrent(
  places: readonly MyanmarPlace[],
  current: string,
): MyanmarPlace[] {
  if (!current || places.some((place) => place.value === current)) {
    return [...places];
  }
  return [{ value: current, my: current }, ...places];
}

function placeOptions(
  places: readonly MyanmarPlace[],
  locale: Locale,
): Array<{ value: string; label: string }> {
  return [...places]
    .sort((left, right) =>
      placeLabel(left, locale).localeCompare(
        placeLabel(right, locale),
        locale === "mm" ? "my" : "en",
      ),
    )
    .map((place) => ({ value: place.value, label: placeLabel(place, locale) }));
}

async function ensureCameraPermission(): Promise<boolean> {
  if (Platform.OS !== "android") {
    return true;
  }
  const permission = PermissionsAndroid.PERMISSIONS.CAMERA;
  if (await PermissionsAndroid.check(permission)) {
    return true;
  }
  const result = await PermissionsAndroid.request(permission);
  return result === PermissionsAndroid.RESULTS.GRANTED;
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
