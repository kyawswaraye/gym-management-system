import { useTx } from "../i18n/useTx";
import { useIsFocused } from "@react-navigation/native";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useRef, useState } from "react";
import { useCameraPermission } from "react-native-vision-camera";

import { ApiError } from "../api/errors";
import { useMemberApi, useOnline } from "../app/providers";
import { formatGymDateTime } from "../dates/gymLocal";
import { useGymTimezone } from "../dates/useGymTimezone";
import { queryKeys } from "../query/keys";
import { readCheckInPayload } from "../scan/payload";
import { scanErrorMessage } from "../scan/errors";
import { ScanPreview } from "./ScanPreview";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Screen } from "../ui/Screen";
import { ScreenBoundary } from "../ui/ScreenBoundary";
import { Text } from "../ui/Text";
import { colors } from "../ui/theme";

export function ScanScreen() {
  const tx = useTx();
  const api = useMemberApi();
  const online = useOnline();
  const focused = useIsFocused();
  const queryClient = useQueryClient();
  const timeZone = useGymTimezone();
  const { hasPermission, requestPermission, canRequestPermission } =
    useCameraPermission();
  const [result, setResult] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [checkingOut, setCheckingOut] = useState(false);
  const inFlight = useRef(false);
  const dashboard = useQuery({
    queryKey: queryKeys.dashboard,
    queryFn: () => api.dashboard(),
  });
  const openVisitId = dashboard.data?.check_in_summary.open_check_in_id ?? null;
  const checkedIn = Boolean(
    dashboard.data?.check_in_summary.currently_checked_in && openVisitId,
  );

  const refreshVisit = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.dashboard }),
      queryClient.invalidateQueries({ queryKey: queryKeys.checkIns }),
    ]);
  }, [queryClient]);

  const submit = useCallback(
    async (raw: string) => {
      if (inFlight.current || checkedIn) {
        return;
      }
      const parsed = readCheckInPayload(raw);
      if (!parsed.ok) {
        setResult(
          parsed.reason === "empty"
            ? tx("Point the camera at the gym check-in code.")
            : tx("That code is not a gym check-in code."),
        );
        return;
      }
      inFlight.current = true;
      setScanning(true);
      try {
        const visit = await api.scan(parsed.payload);
        setResult(
          tx("Checked in at {time}.", {
            time: formatGymDateTime(visit.check_in_at, timeZone),
          }),
        );
        await refreshVisit();
      } catch (error) {
        if (error instanceof ApiError) {
          setResult(tx(scanErrorMessage(error.code, error.message)));
          if (error.code === "CHECK_IN_OPEN") {
            await refreshVisit();
          }
        } else {
          setResult(
            error instanceof Error ? tx(error.message) : tx("Check-in failed."),
          );
        }
      } finally {
        inFlight.current = false;
        setScanning(false);
      }
    },
    [api, checkedIn, refreshVisit, timeZone, tx],
  );

  async function checkOut() {
    if (!openVisitId || checkingOut) {
      return;
    }
    setCheckingOut(true);
    setResult(null);
    try {
      const visit = await api.checkOut(openVisitId);
      setResult(
        tx("Checked out at {time}.", {
          time: formatGymDateTime(
            visit.check_out_at ?? visit.check_in_at,
            timeZone,
          ),
        }),
      );
      await refreshVisit();
    } catch (error) {
      setResult(
        error instanceof Error ? tx(error.message) : tx("Check-out failed."),
      );
    } finally {
      setCheckingOut(false);
    }
  }

  return (
    <Screen scroll={false} title={tx("Check in")}>
      <Text style={{ color: colors.muted }}>
        {tx(
          "Scan the live code on the gym screen. The code changes often, so wait for the current one if a scan fails. If the camera is unavailable, the front desk can check you in.",
        )}
      </Text>
      {!online ? (
        <Text accessibilityRole="alert" style={{ color: colors.muted }}>
          {tx("You are offline, so a scan cannot be sent.")}
        </Text>
      ) : null}
      {checkedIn ? (
        <Card>
          <Text style={{ fontWeight: "700" }}>{tx("You are checked in.")}</Text>
          <Text style={{ color: colors.muted }}>
            {tx(
              "Check out when you leave. If you forget, yesterday's open visit closes automatically when you check in again the next gym day.",
            )}
          </Text>
          <Button
            label={checkingOut ? tx("Checking out…") : tx("Check out")}
            hint={tx("Closes your open gym visit")}
            disabled={!online || checkingOut}
            onPress={() => {
              void checkOut();
            }}
          />
        </Card>
      ) : null}
      {!hasPermission ? (
        <>
          <Text style={{ color: colors.muted }}>
            {tx(
              "Camera access is off. You can still check in with a staff member at the front desk.",
            )}
          </Text>
          {canRequestPermission ? (
            <Button
              label={tx("Allow camera")}
              hint={tx("Asks the phone for camera access")}
              onPress={() => {
                void requestPermission();
              }}
            />
          ) : (
            <Text style={{ color: colors.muted }}>
              {tx(
                "Turn camera access on in system settings, then return here.",
              )}
            </Text>
          )}
        </>
      ) : null}
      {result ? (
        <Card>
          <Text accessibilityLiveRegion="polite" accessibilityRole="alert">
            {result}
          </Text>
        </Card>
      ) : null}
      {hasPermission && focused && !checkedIn ? (
        <ScreenBoundary
          fallback={
            <Text>
              {tx(
                "The camera could not be started. The front desk can still check you in.",
              )}
            </Text>
          }
        >
          <ScanPreview active={online && !scanning} onCode={submit} />
        </ScreenBoundary>
      ) : null}
    </Screen>
  );
}
