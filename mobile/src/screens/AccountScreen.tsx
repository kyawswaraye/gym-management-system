import { useTx } from "../i18n/useTx";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { View } from "react-native";

import type { AccountStackParamList } from "../navigation/types";
import { Card } from "../ui/Card";
import { MenuRow } from "../ui/MenuRow";
import { Screen } from "../ui/Screen";
import { Text } from "../ui/Text";
import { colors, space } from "../ui/theme";

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
    hint: "Language, sign out, and preferences",
  },
];

export function AccountScreen({ navigation }: Props) {
  const tx = useTx();
  return (
    <Screen title={tx("Account")}>
      <Text style={{ color: colors.muted }}>
        {tx(
          "Your membership details stay on this phone only while you are signed in.",
        )}
      </Text>
      <Card padded={false}>
        {links.map((link, index) => (
          <View
            key={link.route}
            style={{
              borderTopColor: colors.border,
              borderTopWidth: index === 0 ? 0 : 1,
              paddingHorizontal: space.md,
            }}
          >
            <MenuRow
              hint={link.hint}
              label={link.label}
              onPress={() => navigation.navigate(link.route)}
            />
          </View>
        ))}
      </Card>
    </Screen>
  );
}
