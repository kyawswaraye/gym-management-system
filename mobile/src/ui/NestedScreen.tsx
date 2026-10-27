import { useNavigation } from "@react-navigation/native";

import { Screen } from "./Screen";

export function NestedScreen(
  props: Omit<Parameters<typeof Screen>[0], "onBack">,
) {
  const navigation = useNavigation();
  return (
    <Screen
      {...props}
      onBack={navigation.canGoBack() ? () => navigation.goBack() : undefined}
    />
  );
}
