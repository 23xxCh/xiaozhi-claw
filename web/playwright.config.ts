import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://192.168.5.49:3000",
    browserName: "chromium",
    channel: "msedge",
    trace: "retain-on-failure",
  },
});
