import { defineConfig } from "@playwright/test";

const webPort = process.env.E2E_WEB_PORT ?? "3010";
const apiPort = process.env.E2E_API_PORT ?? "8010";
const webOrigin = `http://localhost:${webPort}`;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "*.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: Boolean(process.env.CI),
  timeout: 90_000,
  expect: { timeout: 15_000 },
  outputDir: "./test-results",
  reporter: "list",
  use: {
    baseURL: webOrigin,
    trace: "off",
    screenshot: "off",
    video: "off",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  webServer: [
    {
      command: "bash ../scripts/e2e-backend.sh",
      url: `http://127.0.0.1:${apiPort}/api/v1/readiness/`,
      timeout: 180_000,
      reuseExistingServer: false,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: `npm run start -- --hostname localhost --port ${webPort}`,
      url: `${webOrigin}/login`,
      timeout: 120_000,
      reuseExistingServer: false,
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
});
