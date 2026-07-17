import { defineConfig } from "@playwright/test";

// e2e tests assume the full stack (docker-compose postgres/redis/neo4j/backend/worker
// + `npm run dev`) is already running -- see e2e/README.md. They exercise the app
// against a real backend on purpose, not mocks.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: "http://localhost:5173",
    trace: "retain-on-failure",
  },
});
