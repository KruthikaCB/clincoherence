import { expect, test } from "@playwright/test";

test("login links to explicit demo-only registration", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("link", { name: "Create a demo practitioner account", exact: true })).toHaveAttribute("href", "/register");
});

test("disabled backend cannot submit registration", async ({ page }) => {
  await page.route("**/api/v1/auth/demo-registration", route => route.fulfill({ status: 404, json: { detail: "Demo registration unavailable" } }));
  await page.goto("/register");
  await expect(page.getByRole("status")).toContainText("Registration is disabled");
  await expect(page.getByRole("button", { name: "Create demo practitioner" })).toHaveCount(0);
});

for (const outcome of ["success", "duplicate", "uncertain"] as const) test(`registration ${outcome} uses exact schema and no auto login`, async ({ page }) => {
  let posts = 0;
  await page.route("**/api/v1/auth/demo-registration", async route => {
    if (route.request().method() === "GET") return route.fulfill({ json: { enabled: true } });
    posts++;
    expect(route.request().postDataJSON()).toEqual({ identity: "doctor-demo@synthetic.invalid", password: "demo1234", password_confirmation: "demo1234", acknowledge_unverified_demo: true });
    if (outcome === "uncertain") return route.abort("failed");
    return route.fulfill(outcome === "success" ? { status: 201, json: { status: "created", identity_assurance: "unverified_demo" } } : { status: 409, json: { detail: "Demo account cannot be created" } });
  });
  let logins = 0;
  await page.route("**/api/v1/auth/login", route => { logins++; return route.abort(); });
  await page.goto("/register");
  await page.getByLabel("Synthetic account identity").fill("doctor-demo@synthetic.invalid");
  await page.getByLabel("Password", { exact: true }).fill("demo1234");
  await page.getByLabel("Confirm password", { exact: true }).fill("demo1234");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Create demo practitioner" }).click();
  if (outcome === "success") await expect(page.getByRole("status")).toContainText("Demo account created");
  else await expect(page.getByRole("alert", { name: "Registration error" })).toContainText(outcome === "duplicate" ? "Account cannot be created" : "outcome is uncertain");
  await page.waitForTimeout(150);
  expect(posts).toBe(1); expect(logins).toBe(0);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
});

for (const password of ["seven77", "mismatched"]) test(`invalid ${password} rejected locally`, async ({ page }) => {
  let posts = 0;
  await page.route("**/api/v1/auth/demo-registration", route => {
    if (route.request().method() === "POST") posts++;
    return route.fulfill({ json: { enabled: true } });
  });
  await page.goto("/register");
  await page.getByLabel("Synthetic account identity").fill("doctor-demo@synthetic.invalid");
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByLabel("Confirm password", { exact: true }).fill("demo1234");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Create demo practitioner" }).click();
  await expect(page.getByRole("alert", { name: "Registration error" })).toContainText("matching passwords");
  expect(posts).toBe(0);
});

for (const navigate of [false, true]) test(`pending registration is one-shot; navigation ${navigate}`, async ({ page }) => {
  let posts = 0;
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/v1/auth/demo-registration", async route => {
    if (route.request().method() === "GET") return route.fulfill({ json: { enabled: true } });
    posts++;
    await gate;
    await route.fulfill({ status: 201, json: { status: "created", identity_assurance: "unverified_demo" } }).catch(() => {});
  });
  await page.goto("/register");
  await page.getByLabel("Synthetic account identity").fill("doctor-demo@synthetic.invalid");
  await page.getByLabel("Password", { exact: true }).fill("demo1234");
  await page.getByLabel("Confirm password", { exact: true }).fill("demo1234");
  await page.getByRole("checkbox").check();
  const button = page.getByRole("button", { name: "Create demo practitioner" });
  await button.evaluate(element => { (element as HTMLButtonElement).click(); (element as HTMLButtonElement).click(); });
  await expect(page.getByRole("button", { name: "Creating…" })).toBeDisabled();
  await expect.poll(() => posts).toBe(1);
  if (navigate) await page.getByRole("link", { name: "Back to sign in" }).click();
  release();
  if (navigate) {
    await expect(page.getByLabel("Account identity")).toBeVisible();
    await expect(page.getByText("Demo account created.", { exact: false })).toHaveCount(0);
  } else await expect(page.getByRole("status")).toContainText("Demo account created");
  expect(posts).toBe(1);
});
