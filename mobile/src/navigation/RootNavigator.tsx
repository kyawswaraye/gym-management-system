import { useTx } from "../i18n/useTx";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { SafeAreaView } from "react-native-safe-area-context";

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
import { TabIcon, type TabIconName } from "../ui/TabIcon";
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

function HomeStackScreen() {
  return (
    <HomeStack.Navigator screenOptions={stackOptions}>
      <HomeStack.Screen name="Dashboard" component={DashboardScreen} />
    </HomeStack.Navigator>
  );
}

function ScheduleStackScreen() {
  return (
    <ScheduleStack.Navigator screenOptions={stackOptions}>
      <ScheduleStack.Screen name="Schedule" component={ScheduleScreen} />
    </ScheduleStack.Navigator>
  );
}

function CheckInStackScreen() {
  return (
    <CheckInStack.Navigator screenOptions={stackOptions}>
      <CheckInStack.Screen name="Scan" component={ScanScreen} />
    </CheckInStack.Navigator>
  );
}

function InboxStackScreen() {
  return (
    <InboxStack.Navigator screenOptions={stackOptions}>
      <InboxStack.Screen name="Inbox" component={NotificationsScreen} />
    </InboxStack.Navigator>
  );
}

function AccountStackScreen() {
  return (
    <AccountStack.Navigator screenOptions={stackOptions}>
      <AccountStack.Screen name="Account" component={AccountScreen} />
      <AccountStack.Screen name="Profile" component={ProfileScreen} />
      <AccountStack.Screen name="Membership" component={MembershipScreen} />
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
  );
}

function tabIcon(name: TabIconName) {
  return ({ color, size }: { color: string; size: number }) => (
    <TabIcon color={color} name={name} size={size} />
  );
}

export function RootNavigator() {
  const tx = useTx();
  const auth = useAuth();
  if (auth.status === "loading") {
    return (
      <SafeAreaView
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
      </SafeAreaView>
    );
  }
  if (auth.status === "offline") {
    return (
      <SafeAreaView
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
      </SafeAreaView>
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
        tabBarLabelStyle: {
          fontSize: 11,
          fontWeight: "600",
          letterSpacing: 0.2,
        },
        tabBarStyle: {
          backgroundColor: colors.surface,
          borderTopColor: colors.border,
          borderTopWidth: 1,
          paddingTop: 4,
        },
      }}
    >
      <Tabs.Screen
        name="Home"
        component={HomeStackScreen}
        options={{
          tabBarAccessibilityLabel: tx("Home"),
          tabBarIcon: tabIcon("home"),
          tabBarLabel: tx("Home"),
        }}
      />
      <Tabs.Screen
        name="Schedule"
        component={ScheduleStackScreen}
        options={{
          tabBarAccessibilityLabel: tx("Schedule"),
          tabBarIcon: tabIcon("schedule"),
          tabBarLabel: tx("Schedule"),
        }}
      />
      <Tabs.Screen
        name="CheckIn"
        component={CheckInStackScreen}
        options={{
          tabBarAccessibilityLabel: tx("Check in"),
          tabBarIcon: tabIcon("checkin"),
          tabBarLabel: tx("Check in"),
        }}
      />
      <Tabs.Screen
        name="Inbox"
        component={InboxStackScreen}
        options={{
          tabBarAccessibilityLabel: tx("Inbox"),
          tabBarIcon: tabIcon("inbox"),
          tabBarLabel: tx("Inbox"),
        }}
      />
      <Tabs.Screen
        name="Account"
        component={AccountStackScreen}
        options={{
          tabBarAccessibilityLabel: tx("Account"),
          tabBarIcon: tabIcon("account"),
          tabBarLabel: tx("Account"),
        }}
      />
    </Tabs.Navigator>
  );
}
