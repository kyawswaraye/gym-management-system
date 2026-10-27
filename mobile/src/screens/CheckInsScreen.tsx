import { useTx } from "../i18n/useTx";
import { useNavigation } from "@react-navigation/native";

import { useMemberApi } from "../app/providers";
import { formatGymDateTime } from "../dates/gymLocal";
import { queryKeys } from "../query/keys";
import { PagedScreen } from "../ui/PagedScreen";
import { Text } from "../ui/Text";
import { colors } from "../ui/theme";

export function CheckInsScreen() {
  const tx = useTx();
  const api = useMemberApi();
  const navigation = useNavigation();
  return (
    <PagedScreen
      onBack={() => navigation.goBack()}
      title={tx("Check-ins")}
      queryKey={queryKeys.checkIns}
      load={(after) => api.checkIns(after)}
      empty="No check-ins are on file."
      renderItem={(item) => (
        <>
          <Text style={{ fontWeight: "700" }}>
            {formatGymDateTime(item.check_in_at)} · {tx(item.status)}
          </Text>
          <Text style={{ color: colors.muted }}>
            {tx(item.method)}
            {item.check_out_at
              ? ` · ${tx("out {time}", { time: formatGymDateTime(item.check_out_at) })}`
              : tx("· still open")}
          </Text>
        </>
      )}
    />
  );
}
