import { QueryClient } from "@tanstack/react-query";

import { ApiError, NetworkError } from "../api/errors";

function jestWorker(): boolean {
  const runtime = globalThis as {
    process?: { env?: { JEST_WORKER_ID?: string } };
  };
  return Boolean(runtime.process?.env?.JEST_WORKER_ID);
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        gcTime: jestWorker() ? Infinity : 5 * 60 * 1000,
        retry: (failureCount, error) => {
          if (error instanceof NetworkError) {
            return failureCount < 1;
          }
          if (error instanceof ApiError && error.status < 500) {
            return false;
          }
          return failureCount < 2;
        },
        retryDelay: 0,
      },
    },
  });
}
