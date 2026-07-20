import { test, expect } from "@playwright/test";

const EMAIL = "e2e@flowsage.dev";
const PASSWORD = "supersecret123";
const TEAMMATE_EMAIL = "e2e-teammate@flowsage.dev";

async function login(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
}

test("General Settings: edit name, save, and confirm it persists across reload", async ({
  page,
}) => {
  await login(page);

  await page.getByRole("link", { name: "General Settings" }).click();
  await expect(page).toHaveURL(/\/settings\/general/);

  const newName = `E2E Renamed Workspace ${Date.now()}`;
  await page.getByLabel("Project Name").fill(newName);
  await page.getByRole("button", { name: "Save Changes" }).click();
  await expect(page.getByText("Workspace saved.")).toBeVisible();

  await page.reload();
  await expect(page.getByLabel("Project Name")).toHaveValue(newName);
});

test("Team Settings: invite an existing user, change their role, and see the table update", async ({
  page,
}) => {
  await login(page);

  await page.getByRole("link", { name: "Team Access" }).click();
  await expect(page).toHaveURL(/\/settings\/team/);

  // The stack this runs against is real, persistent Postgres, not an
  // ephemeral per-test DB (see e2e/README.md) -- a prior run of this same
  // spec may have already added this teammate, and re-inviting an existing
  // member 409s. Only invite if the row isn't there yet, so the test is
  // idempotent across repeated runs against the same stack.
  let teammateRow = page.locator("tr", { hasText: TEAMMATE_EMAIL });
  if ((await teammateRow.count()) === 0) {
    await page.getByRole("button", { name: "Invite Member" }).click();
    await page.getByLabel("Email").fill(TEAMMATE_EMAIL);
    await page.getByLabel("Role").selectOption("researcher");
    await page.getByRole("button", { name: "Add to Workspace" }).click();
    teammateRow = page.locator("tr", { hasText: TEAMMATE_EMAIL });
  }
  await expect(teammateRow).toBeVisible();

  // Round-trip the role through both values regardless of the row's starting
  // role, so the assertion holds whether this run just created the row
  // (role: researcher) or found it left over from a prior run.
  await teammateRow.locator("select").selectOption("researcher");
  await expect(teammateRow.locator("select")).toHaveValue("researcher");

  await teammateRow.locator("select").selectOption("viewer");
  await expect(teammateRow.locator("select")).toHaveValue("viewer");
});

test("workspace switcher: creating a second workspace makes it selectable, and switching changes dashboard data", async ({
  page,
}) => {
  await login(page);

  // Default workspace was seeded with 5 baseline personas -- the dashboard's
  // "Persona Insights" section should show them, not the empty state.
  await page.goto("/dashboard");
  await expect(page.getByText("No personas loaded yet.")).not.toBeVisible();

  // This chunk doesn't ship a "create workspace" UI (that's a later chunk) --
  // create it directly via the API, sharing the logged-in page's cookies, the
  // same way the plan's manual curl check does.
  const createResponse = await page.request.post("/api/workspaces", {
    data: { name: `E2E Second Workspace ${Date.now()}` },
  });
  expect(createResponse.ok()).toBe(true);
  const secondWorkspace = (await createResponse.json()) as { id: string; name: string };

  // AuthProvider only refetches /auth/me on mount, so reload to pick up the
  // new workspace membership before the switcher can show it.
  await page.reload();

  const switcher = page.getByLabel("Switch workspace");
  await expect(switcher).toBeVisible();
  await switcher.selectOption(secondWorkspace.id);

  // The new workspace has no personas -- confirms the switch actually
  // reloaded dashboard data scoped to the new tenant, not just the UI state.
  await expect(page.getByText("No personas loaded yet.")).toBeVisible();
});
