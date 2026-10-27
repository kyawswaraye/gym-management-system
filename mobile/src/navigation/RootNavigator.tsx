import { useTx } from "../i18n/useTx";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { View } from "react-native";

import { useAuth } from "../app/providers";
import { AccountScreen } from "../screens/AccountScreen";
import { CheckInsScreen } from "../screens/CheckInsScreen";
import { DashboardScreen } from "../screens/DashboardScreen";
import { LegalScreen } from "../screens/LegalScreen";
import { LoginScreen } from "../screens/LoginScreen";
import { MembershipScreen } from "../screens/MembershipScreen";
import { NotificationSettingsScreen } from "../screens/NotificationSettingsScreen";
import { NotificationsScreen } from "../screens/NotificationsScreen";
import { PackagesScreen } from "../screens/PackagesScreen";
import { PaymentsScreen } from "../screens/PaymentsScreen";
import { ProfileScreen } from "../screens/ProfileScreen";
import { ScanScreen } from "../screens/ScanScreen";
import { ScheduleScreen } from "../screens/ScheduleScreen";
import { SettingsScreen } from "../screens/SettingsScreen";
import { BrandMark } from "../ui/BrandMark";
import { Button } from "../ui/Button";
import { ErrorState, LoadingState } from "../ui/StateView";
import { colors, space } from "../ui/theme";
import type {
  AccountStackParamList,
  AuthStackParamList,
  CheckInStackParamList,
  HomeStackParamList,
  InboxStackParamList,
  MainTabParamList,
  ScheduleStackParamList,
} from "./types";

const AuthStack = createNativeStackNavigator<AuthStackParamList>();
const HomeStack = createNativeStackNavigator<HomeStackParamList>();
const ScheduleStack = createNativeStackNavigator<ScheduleStackParamList>();
const CheckInStack = createNativeStackNavigator<CheckInStackParamList>();
const InboxStack = createNativeStackNavigator<InboxStackParamList>();
const AccountStack = createNativeStackNavigator<AccountStackParamList>();
const Tabs = createBottomTabNavigator<MainTabParamList>();

const stackOptions = { headerShown: false } as const;

export function RootNavigator() {
  const tx = useTx();
  const auth = useAuth();
  if (auth.status === "loading") {
    return (
      <View
        style={{
          alignItems: "center",
          backgroundColor: colors.background,
          flex: 1,
          gap: space.md,
          justifyContent: "center",
          padding: space.lg,
        }}
      >
        <BrandMark size={96} />
        <LoadingState label={tx("Checking your session")} />
      </View>
    );
  }
  if (auth.status === "offline") {
    return (
      <View
        style={{
          backgroundColor: colors.background,
          flex: 1,
          gap: space.md,
          justifyContent: "center",
          padding: space.lg,
        }}
      >
        <ErrorState
          error={new Error("The session could not be refreshed.")}
          offline
          onRetry={auth.retry}
        />
        <Button
          label={tx("Sign out of this phone")}
          hint={tx("Removes the saved session from this phone")}
          onPress={() => auth.logout()}
        />
      </View>
    );
  }
  if (auth.status === "anonymous") {
    return (
      <AuthStack.Navigator screenOptions={stackOptions}>
        <AuthStack.Screen name="Login" component={LoginScreen} />
      </AuthStack.Navigator>
    );
  }
  return (
    <Tabs.Navigator
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: colors.muted,
        tabBarLabelStyle: { fontSize: 12, fontWeight: "600" },
        tabBarStyle: {
          backgroundColor: colors.surface,
          borderTopColor: colors.border,
          borderTopWidth: 1,
        },
      }}
    >
      <Tabs.Screen
        name="Home"
        options={{
          tabBarLabel: tx("Home"),
          tabBarAccessibilityLabel: tx("Home"),
        }}
      >
        {() => (
          <HomeStack.Navigator screenOptions={stackOptions}>
            <HomeStack.Screen name="Dashboard" component={DashboardScreen} />
          </HomeStack.Navigator>
        )}
      </Tabs.Screen>
      <Tabs.Screen
        name="Schedule"
        options={{
          tabBarLabel: tx("Schedule"),
          tabBarAccessibilityLabel: tx("Schedule"),
        }}
      >
        {() => (
          <ScheduleStack.Navigator screenOptions={stackOptions}>
            <ScheduleStack.Screen name="Schedule" component={ScheduleScreen} />
          </ScheduleStack.Navigator>
        )}
      </Tabs.Screen>
      <Tabs.Screen
        name="CheckIn"
        options={{
          tabBarLabel: tx("Check in"),
          tabBarAccessibilityLabel: tx("Check in"),
        }}
      >
        {() => (
          <CheckInStack.Navigator screenOptions={stackOptions}>
            <CheckInStack.Screen name="Scan" component={ScanScreen} />
          </CheckInStack.Navigator>
        )}
      </Tabs.Screen>
      <Tabs.Screen
        name="Inbox"
        options={{
          tabBarLabel: tx("Inbox"),
          tabBarAccessibilityLabel: tx("Inbox"),
        }}
      >
        {() => (
          <InboxStack.Navigator screenOptions={stackOptions}>
            <InboxStack.Screen name="Inbox" component={NotificationsScreen} />
          </InboxStack.Navigator>
        )}
      </Tabs.Screen>
      <Tabs.Screen
        name="Account"
        options={{
          tabBarLabel: tx("Account"),
          tabBarAccessibilityLabel: tx("Account"),
        }}
      >
        {() => (
          <AccountStack.Navigator screenOptions={stackOptions}>
            <AccountStack.Screen name="Account" component={AccountScreen} />
            <AccountStack.Screen name="Profile" component={ProfileScreen} />
            <AccountStack.Screen
              name="Membership"
              component={MembershipScreen}
            />
            <AccountStack.Screen name="Payments" component={PaymentsScreen} />
            <AccountStack.Screen name="CheckIns" component={CheckInsScreen} />
            <AccountStack.Screen name="Packages" component={PackagesScreen} />
            <AccountStack.Screen
              name="NotificationSettings"
              component={NotificationSettingsScreen}
            />
            <AccountStack.Screen name="Settings" component={SettingsScreen} />
            <AccountStack.Screen name="Legal" component={LegalScreen} />
          </AccountStack.Navigator>
        )}
      </Tabs.Screen>
    </Tabs.Navigator>
  );
}
