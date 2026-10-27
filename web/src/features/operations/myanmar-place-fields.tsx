"use client";

import { useId } from "react";

import { useLocale, useTx } from "@/i18n/use-tx";
import type { Locale } from "@/i18n/locale";
import {
  citiesForRegion,
  MYANMAR_REGIONS,
  type MyanmarPlace,
} from "@/features/operations/myanmar-locations";

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

function byLabel(locale: Locale) {
  return (left: MyanmarPlace, right: MyanmarPlace) =>
    placeLabel(left, locale).localeCompare(
      placeLabel(right, locale),
      locale === "mm" ? "my" : "en",
    );
}

export function myanmarPlaceText(value: string, locale: Locale): string {
  const text = value.trim();
  if (!text) return "";
  const region = MYANMAR_REGIONS.find((item) => item.value === text);
  if (region) return placeLabel(region, locale);
  for (const item of MYANMAR_REGIONS) {
    const city = item.cities.find((place) => place.value === text);
    if (city) return placeLabel(city, locale);
  }
  return text;
}

export function MyanmarRegionCityFields({
  region,
  city,
  onChange,
  disabled = false,
  named = true,
}: {
  region: string;
  city: string;
  onChange: (next: { region: string; city: string }) => void;
  disabled?: boolean;
  named?: boolean;
}) {
  const tx = useTx();
  const locale = useLocale();
  const hintId = useId();
  const regions = placesWithCurrent(MYANMAR_REGIONS, region).sort(
    byLabel(locale),
  );
  const cities = placesWithCurrent(citiesForRegion(region), city).sort(
    byLabel(locale),
  );
  const cityLocked = disabled || !region;

  function updateRegion(nextRegion: string) {
    const stillValid = citiesForRegion(nextRegion).some(
      (place) => place.value === city,
    );
    onChange({ region: nextRegion, city: stillValid ? city : "" });
  }

  return (
    <>
      <label className="field min-w-0 items-start">
        {tx("Region")}
        <select
          className="input w-0 max-w-full min-w-full"
          disabled={disabled}
          name={named ? "region" : undefined}
          onChange={(event) => updateRegion(event.target.value)}
          value={region}
        >
          <option value="">{tx("Not specified")}</option>
          {regions.map((item) => (
            <option key={item.value} value={item.value}>
              {placeLabel(item, locale)}
            </option>
          ))}
        </select>
      </label>
      <div className="grid min-w-0 content-start gap-1">
        <label className="field min-w-0 items-start">
          {tx("City")}
          <select
            aria-describedby={region ? undefined : hintId}
            className="input w-0 max-w-full min-w-full"
            disabled={cityLocked}
            name={named && region ? "city" : undefined}
            onChange={(event) => onChange({ region, city: event.target.value })}
            value={city}
          >
            <option value="">{tx("Not specified")}</option>
            {cities.map((item) => (
              <option key={item.value} value={item.value}>
                {placeLabel(item, locale)}
              </option>
            ))}
          </select>
        </label>
        {named && !region ? (
          <input name="city" type="hidden" value={city} />
        ) : null}
        {region ? null : (
          <span className="muted" id={hintId}>
            {tx("Choose a region first.")}
          </span>
        )}
      </div>
    </>
  );
}
