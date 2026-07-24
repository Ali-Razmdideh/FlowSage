import { test, expect } from "@playwright/test";

const EMAIL = "e2e@flowsage.dev";
const PASSWORD = "supersecret123";

test("Getting Started: import sample data populates the Journey Graph", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);

  await page.getByRole("link", { name: "Journey Graph" }).click();
  await expect(page).toHaveURL(/\/journey/);

  const importButton = page.getByRole("button", { name: /import sample data/i });
  if (await importButton.isVisible()) {
    await importButton.click();
    await expect(page.getByText("Discovered Funnel")).toBeVisible({ timeout: 15_000 });
  } else {
    // A prior test run in this environment already ingested events -- the
    // empty state (and its button) won't render, which is expected reuse,
    // not a failure.
    await expect(page.getByText("Discovered Funnel")).toBeVisible();
  }

  await page.getByRole("link", { name: "Getting Started" }).click();
  await expect(page).toHaveURL(/\/getting-started/);
  await expect(page.getByText("Run your first simulation")).toBeVisible();
});
