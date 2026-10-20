import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import type { AccountStackParamList } from "../navigation/types";
import { Screen } from "../ui/Screen";
import { Text } from "../ui/Text";

type Props = NativeStackScreenProps<AccountStackParamList, "Legal">;

const copy = {
  privacy: {
    title: "Privacy",
    body: "Gym Member signs you in to one linked membership, shows your profile, membership, payments, check-ins, packages, and schedule, and can scan an onsite check-in code. The camera is used only when you scan that code or choose a profile photo. Push is optional. The app does not include advertising or an analytics SDK. Refresh credentials stay in the device keystore. Access credentials stay in memory for the current session.",
  },
  terms: {
    title: "Terms",
    body: "Use of this app follows your gym membership. Schedules, prices, remaining sessions, and check-in results come from the gym. A denied camera or notification permission does not remove the rest of the app. Signing out revokes this phone's session.",
  },
} as const;

export function LegalScreen({ route }: Props) {
  const document = copy[route.params.document];
  return (
    <Screen title={document.title}>
      <Text>{document.body}</Text>
    </Screen>
  );
}
