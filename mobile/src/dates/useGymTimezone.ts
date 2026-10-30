import { useQuery } from "@tanstack/react-query";

import { useMemberApi } from "../app/providers";
import { queryKeys } from "../query/keys";

import { FALLBACK_GYM_TIMEZONE } from "./gymLocal";

export function useGymTimezone(): string {
  const api = useMemberApi();
  const dashboard = useQuery({
    queryKey: queryKeys.dashboard,
    queryFn: () => api.dashboard(),
    staleTime: 5 * 60 * 1000,
  });
  return dashboard.data?.gym.timezone ?? FALLBACK_GYM_TIMEZONE;
}
