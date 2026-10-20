import { onlineManager, QueryClientProvider } from "@tanstack/react-query";
import NetInfo from "@react-native-community/netinfo";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { createApiClient, type ApiClient } from "../api/client";
import { createMemberApi, type MemberApi } from "../api/memberApi";
import { createTokenMemory } from "../auth/memory";
import { createSecureStore } from "../auth/secureStore";
import {
  createSession,
  type Session,
  type SessionStatus,
} from "../auth/session";
import { env } from "../config/env";
import { createQueryClient } from "../query/client";

type AuthValue = {
  status: SessionStatus;
  login: Session["login"];
  logout: Session["logout"];
  retry: () => Promise<void>;
};

const AuthContext = createContext<AuthValue | null>(null);
const MemberApiContext = createContext<MemberApi | null>(null);
const OnlineContext = createContext(true);

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("Auth is not available.");
  }
  return value;
}

export function useMemberApi(): MemberApi {
  const value = useContext(MemberApiContext);
  if (!value) {
    throw new Error("The member API is not available.");
  }
  return value;
}

export function useOnline(): boolean {
  return useContext(OnlineContext);
}

export function AppProviders({
  children,
  sessionStatus,
  api,
  auth,
}: {
  children: ReactNode;
  sessionStatus?: SessionStatus;
  api?: MemberApi;
  auth?: AuthValue;
}) {
  const runtime = useMemo(() => createRuntime(), []);
  const [status, setStatus] = useState<SessionStatus>(
    sessionStatus ?? "loading",
  );
  const [online, setOnline] = useState(true);

  useEffect(() => {
    if (sessionStatus) {
      return;
    }
    let active = true;
    runtime.session.bootstrap().then((next) => {
      if (active) {
        setStatus(next);
      }
    });
    return () => {
      active = false;
    };
  }, [runtime.session, sessionStatus]);

  useEffect(() => {
    return NetInfo.addEventListener((state) => {
      const connected =
        state.isConnected !== false && state.isInternetReachable !== false;
      setOnline(connected);
      onlineManager.setOnline(connected);
    });
  }, []);

  const authValue = auth ?? {
    status,
    login: async (email: string, password: string) => {
      await runtime.session.login(email, password);
      setStatus("signed-in");
    },
    logout: async () => {
      const result = await runtime.session.logout();
      setStatus("anonymous");
      return result;
    },
    retry: async () => {
      setStatus("loading");
      setStatus(await runtime.session.bootstrap());
    },
  };

  return (
    <QueryClientProvider client={runtime.queryClient}>
      <OnlineContext.Provider value={online}>
        <MemberApiContext.Provider value={api ?? runtime.memberApi}>
          <AuthContext.Provider value={authValue}>
            {children}
          </AuthContext.Provider>
        </MemberApiContext.Provider>
      </OnlineContext.Provider>
    </QueryClientProvider>
  );
}

function createRuntime(): {
  session: Session;
  memberApi: MemberApi;
  queryClient: ReturnType<typeof createQueryClient>;
  client: ApiClient;
} {
  const memory = createTokenMemory();
  const store = createSecureStore();
  const queryClient = createQueryClient();
  let sessionWipe = async () => {};
  const client = createApiClient({
    baseUrl: env.apiBaseUrl,
    getAccessToken: () => memory.getAccessToken(),
    setAccessToken: (token) => memory.setAccessToken(token),
    getRefreshToken: () => store.getRefreshToken(),
    setRefreshToken: (token) => store.setRefreshToken(token),
    onSessionRevoked: () => sessionWipe(),
  });
  const session = createSession({
    api: client,
    store,
    memory,
    clearCache: () => queryClient.clear(),
  });
  sessionWipe = () => session.wipe();
  return { session, memberApi: createMemberApi(client), queryClient, client };
}
