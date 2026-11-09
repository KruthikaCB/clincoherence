import { test, expect, Page } from "@playwright/test";

const token = "A".repeat(43);
const code = "C".repeat(32);
const password = "synthetic-password-only";
const identity = "invited.patient@synthetic.invalid";
const notice = "Prototype-only synthetic-user identity verification; not production-grade. Deliver the verification code separately from the invitation link. Activation does not grant practitioner consent.";
const inspected = { valid: true, expires_at: "2099-01-01T10:00:00Z", prototype_notice: notice };
const activated = { account_id: "11111111-1111-4111-8111-111111111111", patient_id: "22222222-2222-4222-8222-222222222222", prototype_notice: notice };
const error = (page: Page) => page.getByRole("alert", { name: "Activation error" });
const button = (page: Page) => page.getByRole("button", { name: "Activate patient account", exact: true });

async function inspect(page: Page) {
  const calls: string[] = [];
  await page.route("**/api/v1/**", route => {
    const request = route.request();
    calls.push(new URL(request.url()).pathname);
    if (request.url().endsWith("/patient-invitations/inspect")) {
      expect(request.method()).toBe("POST");
      expect(request.postDataJSON()).toEqual({ token });
      expect(request.headers().authorization).toBeUndefined();
      return route.fulfill({ json: inspected });
    }
    return route.fulfill({ status: 500, json: { detail: "Unexpected API call" } });
  });
  await page.goto(`/activate#token=${token}`);
  await expect(button(page)).toBeVisible();
  return calls;
}

async function fill(page: Page, values: { identity?: string; code?: string; password?: string } = {}) {
  await page.getByLabel("Intended synthetic identity").fill(values.identity ?? identity);
  await page.getByLabel("Separate verification code", { exact: true }).fill(values.code ?? code);
  await page.getByLabel("New password", { exact: true }).fill(values.password ?? password);
}

test("inspection removes fragment, survives development Strict Mode and uses body-only secrets", async ({ page }) => {
  const urls: string[] = [];
  page.on("request", request => urls.push(request.url()));
  const calls = await inspect(page);
  await expect(page).toHaveURL(/\/activate$/);
  await expect(page.getByText(notice, { exact: true })).toBeVisible();
  await expect(page.locator("time")).toHaveAttribute("datetime", inspected.expires_at);
  await page.waitForTimeout(200);
  expect(calls).toEqual(["/api/v1/patient-invitations/inspect"]);
  expect(urls.every(url => !url.includes(token) && !url.includes("token="))).toBe(true);
  expect(JSON.stringify(await page.evaluate(() => window.history.state))).not.toContain(token);
  await expect(page.locator("body")).not.toContainText(token);
});

test("explicit activation has exact payload, no login/consent and ordinary sign-in link", async ({ page }) => {
  const calls = await inspect(page);
  await page.route("**/patient-invitations/activate", route => {
    calls.push("/api/v1/patient-invitations/activate");
    expect(route.request().method()).toBe("POST");
    expect(route.request().postDataJSON()).toEqual({ token, identity, verification_code: code, password });
    expect(route.request().headers().authorization).toBeUndefined();
    expect(route.request().url()).not.toContain(token);
    return route.fulfill({ status: 201, json: activated });
  });
  await fill(page);
  await button(page).click();
  await expect(page.getByRole("status")).toContainText("Account activated");
  await expect(page.getByRole("status")).toContainText("No practitioner consent has been granted");
  expect(calls).toEqual(["/api/v1/patient-invitations/inspect", "/api/v1/patient-invitations/activate"]);
  await expect(page).toHaveURL(/\/activate$/);
  await expect(page.getByLabel("New password", { exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length, document.cookie])).toEqual([0, 0, ""]);
  await page.getByRole("link", { name: "Go to ordinary sign in" }).click();
  await expect(page.getByLabel("Account identity")).toBeVisible();
});

for (const state of ["invalid", "expired", "revoked", "consumed"]) test(`${state} inspection is generic and does not activate`, async ({ page }) => {
  let calls = 0;
  await page.route("**/api/v1/**", route => {
    calls++;
    expect(route.request().url()).toMatch(/\/inspect$/);
    return route.fulfill({ status: 400, json: { detail: `${state} PRIVATE ${token}` } });
  });
  await page.goto(`/activate#token=${token}`);
  await expect(error(page)).toContainText("Invitation unavailable or verification failed");
  await expect(button(page)).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText("PRIVATE");
  expect(calls).toBe(1);
  await expect(page).toHaveURL(/\/activate$/);
});

for (const fragment of ["", "#token=short", `#token=${token}&token=${token}`, `#token=${token}&extra=1`]) test(`missing/malformed fragment ${fragment.length} causes no API request`, async ({ page }) => {
  let calls = 0;
  await page.route("**/api/v1/**", route => { calls++; return route.fulfill({ status: 500 }); });
  await page.goto(`/activate${fragment}`);
  await expect(error(page)).toContainText("Invitation unavailable");
  expect(calls).toBe(0);
  await expect(page).toHaveURL(/\/activate$/);
});

for (const incorrect of ["identity", "code"]) test(`incorrect ${incorrect} is generic and clears sensitive inputs`, async ({ page }) => {
  await inspect(page);
  let calls = 0;
  await page.route("**/patient-invitations/activate", route => {
    calls++;
    return route.fulfill({ status: 400, json: { detail: `PRIVATE ${identity} ${code}` } });
  });
  await fill(page, incorrect === "identity" ? { identity: "wrong@synthetic.invalid" } : { code: "wrong-code" });
  await button(page).click();
  await expect(error(page)).toContainText("Invitation unavailable or verification failed");
  await expect(page.getByLabel("New password", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("Separate verification code", { exact: true })).toHaveValue("");
  await expect(page.locator("body")).not.toContainText("PRIVATE");
  expect(calls).toBe(1);
});

for (const value of ["short", "x".repeat(1025)]) test(`password length ${value.length} rejected locally without activation`, async ({ page }) => {
  const calls = await inspect(page);
  await fill(page, { password: value }); await button(page).click();
  await expect(error(page)).toContainText("12 to 1024 characters");
  expect(calls).toEqual(["/api/v1/patient-invitations/inspect"]);
});

test("non-synthetic identity is rejected locally", async ({ page }) => {
  const calls = await inspect(page);
  await fill(page, { identity: "person@example.test" }); await button(page).click();
  await expect(error(page)).toContainText("intended synthetic.invalid identity");
  expect(calls).toHaveLength(1);
});

test("duplicate clicks do not duplicate activation or optimistically succeed", async ({ page }) => {
  await inspect(page);
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let calls = 0;
  await page.route("**/patient-invitations/activate", async route => { calls++; await gate; await route.fulfill({ status: 201, json: activated }); });
  await fill(page);
  await button(page).evaluate((element: HTMLButtonElement) => { element.click(); element.click(); });
  await expect.poll(() => calls).toBe(1);
  await expect(page.getByRole("button", { name: "Activating...", exact: true })).toBeDisabled();
  await expect(page.getByText("Account activated.", { exact: true })).toHaveCount(0);
  release();
  await expect(page.getByRole("status")).toContainText("Account activated");
  expect(calls).toBe(1);
});

for (const failure of ["network", "server", "malformed"]) test(`uncertain ${failure} outcome does not automatically retry`, async ({ page }) => {
  await inspect(page);
  let calls = 0;
  await page.route("**/patient-invitations/activate", route => {
    calls++;
    if (failure === "network") return route.abort("failed");
    if (failure === "malformed") return route.fulfill({ status: 201, contentType: "application/json", body: "{" });
    return route.fulfill({ status: 503, json: { detail: "PRIVATE" } });
  });
  await fill(page); await button(page).click();
  await expect(error(page)).toContainText("Activation outcome is uncertain");
  await expect(button(page)).toHaveCount(0);
  await page.waitForTimeout(200); expect(calls).toBe(1);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length, document.cookie])).toEqual([0, 0, ""]);
});

test("activation timeout does not replay", async ({ page }) => {
  await inspect(page); await page.clock.install();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let calls = 0;
  await page.route("**/patient-invitations/activate", async route => { calls++; await gate; await route.abort().catch(() => {}); });
  await fill(page); await button(page).click();
  await expect.poll(() => calls).toBe(1);
  await page.clock.fastForward(20001);
  await expect(error(page)).toContainText("Activation outcome is uncertain");
  expect(calls).toBe(1); release();
});

for (const phase of ["inspect", "activate"]) test(`navigation during pending ${phase} cannot restore activation state`, async ({ page }) => {
  if (phase === "activate") await inspect(page);
  let entered!: () => void;
  let release!: () => void;
  const started = new Promise<void>(resolve => { entered = resolve; });
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route(`**/patient-invitations/${phase}`, async route => { entered(); await gate; await route.fulfill({ status: phase === "inspect" ? 200 : 201, json: phase === "inspect" ? inspected : activated }).catch(() => {}); });
  if (phase === "inspect") await page.goto(`/activate#token=${token}`);
  else { await fill(page); await button(page).click(); }
  await started;
  await page.getByRole("link", { name: "Go to ordinary sign in" }).click();
  await expect(page.getByLabel("Account identity")).toBeVisible();
  release();
  await expect(page.getByRole("heading", { name: "Patient activation", exact: true })).toHaveCount(0);
  await expect(page.getByText(/Account activated\./)).toHaveCount(0);
});

test("malformed inspection never enables activation", async ({ page }) => {
  await page.route("**/patient-invitations/inspect", route => route.fulfill({ json: { ...inspected, valid: false, token } }));
  await page.goto(`/activate#token=${token}`);
  await expect(error(page)).toContainText("Invitation unavailable");
  await expect(button(page)).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText(token);
});

test("same-page fragment changes clear the new secret and invalidate the workflow", async ({ page }) => {
  const calls = await inspect(page);
  await fill(page);
  await page.evaluate(() => { window.location.hash = `token=${"B".repeat(43)}`; });
  await expect(page).toHaveURL(/\/activate$/);
  await expect(error(page)).toContainText("Invitation unavailable");
  await expect(button(page)).toHaveCount(0);
  expect(calls).toEqual(["/api/v1/patient-invitations/inspect"]);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length, document.cookie])).toEqual([0, 0, ""]);
});
