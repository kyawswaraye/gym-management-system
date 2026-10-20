import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import type { AccountStackParamList } from "../navigation/types";
import { Button } from "../ui/Button";
import { Screen } from "../ui/Screen";
import { Text } from "../ui/Text";

type Props = NativeStackScreenProps<AccountStackParamList, "Account">;

const links: Array<{
  route:
    | "Profile"
    | "Membership"
    | "Payments"
    | "CheckIns"
    | "Packages"
    | "Settings";
  label: string;
  hint: string;
}> = [
  {
    route: "Profile",
    label: "Profile",
    hint: "Edits contact details and your photo",
  },
  {
    route: "Membership",
    label: "Membership",
    hint: "Shows the current membership",
  },
  { route: "Payments", label: "Payments", hint: "Shows payment history" },
  { route: "CheckIns", label: "Check-ins", hint: "Shows check-in history" },
  { route: "Packages", label: "Packages", hint: "Shows service packages" },
  {
    route: "Settings",
    label: "Settings",
    hint: "Signs out and shows app details",
  },
];

export function AccountScreen({ navigation }: Props) {
  return (
    <Screen title="Account">
      <Text>
        Your membership details stay on this phone only while you are signed in.
      </Text>
      {links.map((link) => (
        <Button
          key={link.route}
          label={link.label}
          hint={link.hint}
          variant="secondary"
          onPress={() => navigation.navigate(link.route)}
        />
      ))}
    </Screen>
  );
}
