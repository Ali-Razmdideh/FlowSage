import { test, expect } from "@playwright/test";

const EMAIL = "e2e@flowsage.dev";
const PASSWORD = "supersecret123";

test("Security Logs: login shows up in the audit trail", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);

  await page.getByRole("link", { name: "Security" }).click();
  await expect(page).toHaveURL(/\/settings\/security/);
  await expect(page.getByText("auth.login").first()).toBeVisible();
});
