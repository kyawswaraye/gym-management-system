import { useTx } from "../i18n/useTx";
import { useNavigation } from "@react-navigation/native";

import { useMemberApi } from "../app/providers";
import { formatGymDateTime } from "../dates/gymLocal";
import { useGymTimezone } from "../dates/useGymTimezone";
import { queryKeys } from "../query/keys";
import { displayText } from "../ui/display";
import { PagedScreen } from "../ui/PagedScreen";
import { Text } from "../ui/Text";
import { colors } from "../ui/theme";

export function CheckInsScreen() {
  const tx = useTx();
  const api = useMemberApi();
  const navigation = useNavigation();
  const timeZone = useGymTimezone();
  return (
    <PagedScreen
      onBack={() => navigation.goBack()}
      title={tx("Check-ins")}
      queryKey={queryKeys.checkIns}
      load={(after) => api.checkIns(after)}
      empty="No check-ins are on file."
      renderItem={(item) => {
        const status = displayText(item?.status);
        const method = displayText(item?.method);
        const arrived = formatGymDateTime(
          displayText(item?.check_in_at),
          timeZone,
        );
        const left = formatGymDateTime(
          displayText(item?.check_out_at),
          timeZone,
        );
        return (
          <>
            <Text style={{ fontWeight: "700" }}>
              {arrived}
              {status ? ` · ${tx(status)}` : ""}
            </Text>
            <Text style={{ color: colors.muted }}>
              {method ? tx(method) : ""}
              {left
                ? ` · ${tx("out {time}", { time: left })}`
                : tx("· still open")}
            </Text>
          </>
        );
      }}
    />
  );
}
