import { View } from "react-native";

export type TabIconName = "home" | "schedule" | "checkin" | "inbox" | "account";

export function TabIcon({
  name,
  color,
  size = 24,
}: {
  name: TabIconName;
  color: string;
  size?: number;
}) {
  const box = {
    width: size,
    height: size,
    alignItems: "center" as const,
    justifyContent: "center" as const,
  };
  if (name === "home") {
    return (
      <View style={box}>
        <View
          style={{
            borderBottomColor: color,
            borderBottomWidth: 8,
            borderLeftColor: "transparent",
            borderLeftWidth: 9,
            borderRightColor: "transparent",
            borderRightWidth: 9,
            height: 0,
            width: 0,
          }}
        />
        <View
          style={{
            borderColor: color,
            borderTopWidth: 0,
            borderWidth: 2,
            height: 10,
            marginTop: -1,
            width: 14,
          }}
        />
      </View>
    );
  }
  if (name === "schedule") {
    return (
      <View style={box}>
        <View
          style={{
            borderColor: color,
            borderRadius: 3,
            borderWidth: 2,
            height: 16,
            overflow: "hidden",
            width: 16,
          }}
        >
          <View style={{ backgroundColor: color, height: 4 }} />
          <View
            style={{
              flexDirection: "row",
              flexWrap: "wrap",
              gap: 2,
              padding: 2,
            }}
          >
            <View style={{ backgroundColor: color, height: 2, width: 2 }} />
            <View style={{ backgroundColor: color, height: 2, width: 2 }} />
            <View style={{ backgroundColor: color, height: 2, width: 2 }} />
          </View>
        </View>
      </View>
    );
  }
  if (name === "checkin") {
    const arm = { backgroundColor: color, position: "absolute" as const };
    return (
      <View style={box}>
        <View style={{ height: 16, width: 16 }}>
          <View style={[arm, { height: 2, left: 0, top: 0, width: 6 }]} />
          <View style={[arm, { height: 6, left: 0, top: 0, width: 2 }]} />
          <View style={[arm, { height: 2, right: 0, top: 0, width: 6 }]} />
          <View style={[arm, { height: 6, right: 0, top: 0, width: 2 }]} />
          <View style={[arm, { bottom: 0, height: 2, left: 0, width: 6 }]} />
          <View style={[arm, { bottom: 0, height: 6, left: 0, width: 2 }]} />
          <View style={[arm, { bottom: 0, height: 2, right: 0, width: 6 }]} />
          <View style={[arm, { bottom: 0, height: 6, right: 0, width: 2 }]} />
        </View>
      </View>
    );
  }
  if (name === "inbox") {
    return (
      <View style={box}>
        <View
          style={{
            borderColor: color,
            borderRadius: 3,
            borderWidth: 2,
            height: 12,
            width: 18,
          }}
        >
          <View
            style={{
              borderBottomColor: color,
              borderBottomWidth: 2,
              borderLeftColor: "transparent",
              borderLeftWidth: 7,
              borderRightColor: "transparent",
              borderRightWidth: 7,
              height: 0,
              width: 0,
            }}
          />
        </View>
      </View>
    );
  }
  return (
    <View style={box}>
      <View
        style={{
          borderColor: color,
          borderRadius: 6,
          borderWidth: 2,
          height: 8,
          width: 8,
        }}
      />
      <View
        style={{
          borderColor: color,
          borderTopLeftRadius: 8,
          borderTopRightRadius: 8,
          borderWidth: 2,
          height: 7,
          marginTop: 2,
          width: 14,
        }}
      />
    </View>
  );
}
