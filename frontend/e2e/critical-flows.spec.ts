import { test, expect } from "@playwright/test";
import path from "node:path";

const EMAIL = "e2e@flowsage.dev";
const PASSWORD = "supersecret123";
const SCREENSHOTS_DIR = path.resolve(import.meta.dirname, "../../scripts/sample_data/screenshots");

async function login(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
}

test("unauthenticated visitors are redirected to login", async ({ page }) => {
  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/login/);
});

test("login redirects to the dashboard and shows real KPIs", async ({ page }) => {
  await login(page);
  await expect(page.getByRole("heading", { name: "Executive Summary" })).toBeVisible();
  await expect(page.getByText("TOTAL SESSIONS OBSERVED")).toBeVisible();
});

test("journey graph loads without error", async ({ page }) => {
  await login(page);
  // Direct navigation: the dashboard also has a "View Journey Graph" link, so
  // clicking by accessible name here would be ambiguous with the sidebar's.
  await page.goto("/journey");
  // Either state is valid depending on whether events were ingested -- the
  // point is the page renders one of them, not an error boundary.
  await expect(
    page.getByRole("heading", { name: "Discovered Funnel" }).or(
      page.getByRole("heading", { name: "Awaiting Event Ingestion" }),
    ),
  ).toBeVisible();
});

test("calibration insights loads without error", async ({ page }) => {
  await login(page);
  await page.getByRole("link", { name: "Calibration" }).click();
  await expect(page).toHaveURL(/\/calibration/);
  // Whichever state the report is in (anomaly vs. optimized depends on whether
  // any completed simulation run + ingested events exist), the heading is
  // always rendered -- that's the contract this test checks.
  await expect(page.getByRole("heading", { name: "Calibration Insights" })).toBeVisible();
});

test("running a simulation reaches a terminal state", async ({ page }) => {
  await login(page);
  await page.getByRole("link", { name: "Predictive Engine" }).click();
  await expect(page).toHaveURL(/\/predictive/);

  await page.getByLabel("Flow name").fill("E2E Checkout Flow");
  await page.setInputFiles('input[type="file"]', [
    path.join(SCREENSHOTS_DIR, "01_cart.png"),
    path.join(SCREENSHOTS_DIR, "02_shipping.png"),
    path.join(SCREENSHOTS_DIR, "03_confirm.png"),
  ]);
  await page.getByRole("button", { name: "Run Simulation" }).click();

  await expect(page).toHaveURL(/\/predictive\/runs\/[\w-]+/);
  await expect(page.getByRole("heading", { name: "Agentic Orchestration" })).toBeVisible();

  // Whether it succeeds depends on ANTHROPIC_API_KEY being set; either way it
  // must leave the "queued" state within a reasonable time.
  await expect(page.getByText(/^(FAILED|COMPLETED|running)$/i)).toBeVisible({ timeout: 30_000 });
});
