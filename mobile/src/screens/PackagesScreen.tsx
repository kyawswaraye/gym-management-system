import { useTx } from "../i18n/useTx";
import { useNavigation } from "@react-navigation/native";

import { useMemberApi } from "../app/providers";
import { formatGymDate, formatRemainingSessions } from "../dates/gymLocal";
import { queryKeys } from "../query/keys";
import { displayText } from "../ui/display";
import { PagedScreen } from "../ui/PagedScreen";
import { Text } from "../ui/Text";
import { colors } from "../ui/theme";

export function PackagesScreen() {
  const tx = useTx();
  const api = useMemberApi();
  const navigation = useNavigation();
  return (
    <PagedScreen
      onBack={() => navigation.goBack()}
      title={tx("Packages")}
      queryKey={queryKeys.packages}
      load={(after) => api.servicePackages(after)}
      empty="No service packages are on file."
      renderItem={(item) => {
        const name = displayText(item?.package_name);
        const kind = displayText(item?.package_type);
        const status = displayText(item?.status);
        return (
          <>
            <Text style={{ fontWeight: "700" }}>
              {name}
              {kind ? ` · ${tx(kind)}` : ""}
            </Text>
            {status ? (
              <Text style={{ color: colors.muted }}>
                {tx("Status")} {tx(status)}
              </Text>
            ) : null}
            <Text>
              {tx("Remaining sessions")}{" "}
              {formatRemainingSessions(item?.remaining_sessions)}
            </Text>
            <Text style={{ color: colors.muted }}>
              {formatGymDate(displayText(item?.valid_from))} {tx("to")}{" "}
              {formatGymDate(displayText(item?.valid_until))}
            </Text>
            {item?.complimentary ? <Text>{tx("Complimentary")}</Text> : null}
          </>
        );
      }}
    />
  );
}
