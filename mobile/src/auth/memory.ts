export type TokenMemory = {
  getAccessToken(): string | null;
  setAccessToken(token: string | null): void;
};

export function createTokenMemory(): TokenMemory {
  let accessToken: string | null = null;
  return {
    getAccessToken: () => accessToken,
    setAccessToken: (token) => {
      accessToken = token;
    },
  };
}
