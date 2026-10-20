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
import { Button } from "../ui/Button";
import { ErrorState, LoadingState } from "../ui/StateView";
import { colors } from "../ui/theme";
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
  const auth = useAuth();
  if (auth.status === "loading") {
    return (
      <View
        style={{
          flex: 1,
          justifyContent: "center",
          padding: 24,
          backgroundColor: colors.background,
        }}
      >
        <LoadingState label="Checking your session" />
      </View>
    );
  }
  if (auth.status === "offline") {
    return (
      <View
        style={{
          flex: 1,
          justifyContent: "center",
          padding: 24,
          gap: 12,
          backgroundColor: colors.background,
        }}
      >
        <ErrorState
          error={new Error("The session could not be refreshed.")}
          offline
          onRetry={auth.retry}
        />
        <Button
          label="Sign out of this phone"
          hint="Removes the saved session from this phone"
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
      }}
    >
      <Tabs.Screen
        name="Home"
        options={{ tabBarLabel: "Home", tabBarAccessibilityLabel: "Home" }}
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
          tabBarLabel: "Schedule",
          tabBarAccessibilityLabel: "Schedule",
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
          tabBarLabel: "Check in",
          tabBarAccessibilityLabel: "Check in",
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
        options={{ tabBarLabel: "Inbox", tabBarAccessibilityLabel: "Inbox" }}
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
          tabBarLabel: "Account",
          tabBarAccessibilityLabel: "Account",
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
