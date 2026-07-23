import { test, expect } from "@playwright/test";

const EMAIL = "e2e@flowsage.dev";
const PASSWORD = "supersecret123";

async function login(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
}

test("Integrations: connect Slack via the UI and see it persist across reload", async ({
  page,
}) => {
  await login(page);

  await page.getByRole("link", { name: "Integrations" }).click();
  await expect(page).toHaveURL(/\/settings\/integrations/);

  const slackCard = page.getByRole("heading", { name: "Slack" }).locator("..");
  // Wait for the card to finish its initial getSlackStatus() fetch (either button
  // appears) before deciding which state it's in -- an instant, non-waiting check
  // here races the fetch and can misread "still loading" as "not connected".
  await expect(
    slackCard.getByRole("button", { name: /^(Connect|Disconnect)$/ })
  ).toBeVisible();
  const alreadyConnected = await slackCard.getByRole("button", { name: "Disconnect" }).isVisible();
  if (!alreadyConnected) {
    await slackCard.getByRole("button", { name: "Connect" }).click();
    await page.getByLabel("Webhook URL").fill("https://hooks.slack.com/services/e2e/test/hook");
    await page.getByRole("button", { name: "Save" }).click();
  }
  await expect(slackCard.getByRole("button", { name: "Disconnect" })).toBeVisible();

  await page.reload();
  await expect(slackCard.getByRole("button", { name: "Disconnect" })).toBeVisible();
});

test("Integrations: create an API key and see the raw key revealed once", async ({ page }) => {
  await login(page);
  await page.goto("/settings/integrations");

  await page.getByRole("button", { name: "Create key" }).click();
  await page.getByLabel("Key name").fill(`e2e-key-${Date.now()}`);
  await page.getByRole("button", { name: "Generate" }).click();

  await expect(page.locator("code", { hasText: /^fs_live_/ })).toBeVisible();
  await page.getByRole("button", { name: "Done" }).click();
});

test("Integrations: add a webhook and see it in the table", async ({ page }) => {
  await login(page);
  await page.goto("/settings/integrations");

  const url = `https://example.com/e2e-hook-${Date.now()}`;
  await page.getByRole("button", { name: "Add webhook" }).click();
  await page.getByLabel("Webhook URL").fill(url);
  await page.getByRole("button", { name: "Add", exact: true }).click();

  await expect(page.getByText(url)).toBeVisible();
});
