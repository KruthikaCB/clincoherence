import { patients, selectPatient, selected } from "./dashboard";
import { expect, Page, test } from "@playwright/test";

const patient = "11111111-1111-4111-8111-111111111111";
const existing = "44444444-4444-4444-8444-444444444444";
const actor = "22222222-2222-4222-8222-222222222222";
const profile = "33333333-3333-4333-8333-333333333333";
const demo = { patient_id: patient, identifier: "demo-patient-1", label: "Synthetic Demo Patient" };

async function setup(page: Page, options: { disabled?: boolean; uncertain?: boolean; hold?: Promise<void>; initial?: boolean } = {}) {
  let created = false, posts = 0;
  await page.route("**/api/v1/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/login")) return route.fulfill({ json: { access_token: "synthetic-token", token_type: "bearer" } });
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { id: actor, role: "practitioner" } });
    if (path.endsWith("/auth/profile")) return route.fulfill({ json: { profile_id: profile, role: "practitioner" } });
    if (path === "/api/v1/patients") return route.fulfill({ json: [...(options.initial ? [{ patient_id: existing }] : []), ...(created ? [{ patient_id: patient }] : [])] });
    if (path === "/api/v1/demo-patients") {
      if (options.disabled) return route.fulfill({ status: 404, json: { detail: "Demo patients unavailable" } });
      if (request.method() === "GET") return route.fulfill({ json: created ? [demo] : [] });
      posts++;
      expect(request.postDataJSON()).toEqual({ identifier: demo.identifier });
      expect(request.headers().authorization).toBe("Bearer synthetic-token");
      if (options.hold) await options.hold;
      if (options.uncertain) return route.abort();
      created = true;
      return route.fulfill({ status: 201, json: demo }).catch(() => {});
    }
    if (path.endsWith("/facts") || path.endsWith("/confirmations")) return route.fulfill({ json: [] });
    return route.fulfill({ status: 404, json: { detail: "Unavailable" } });
  });
  await page.goto("/");
  await page.getByLabel("Account identity").fill("doctor-demo@synthetic.invalid");
  await page.getByLabel("Password", { exact: true }).fill("demo1234");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByText("Practitioner account", { exact: true })).toBeVisible();
  return () => posts;
}

test("creator can add and immediately select a clearly labeled synthetic patient", async ({ page }) => {
  const posts = await setup(page);
  await page.getByRole("button", { name: "Add Patient", exact: true }).click();
  await page.getByRole("button", { name: "+ Add Synthetic Demo Patient", exact: true }).click();
  await page.getByLabel("Synthetic patient identifier").fill(demo.identifier);
  await page.getByRole("button", { name: "Create Patient", exact: true }).click();
  await selected(page, patient);
  await expect(patients(page).filter({ hasText: demo.identifier })).toBeVisible();
  await expect(page.getByText("Synthetic Demo", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Clinical note")).toBeVisible();
  await expect(page.getByRole("button", { name: "Clinical History", exact: true })).toBeEnabled();
  expect(posts()).toBe(1);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
});

test("disabled backend offers no add action", async ({ page }) => {
  await setup(page, { disabled: true });
  await expect(page.getByText("No patients yet", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Add Patient", exact: true })).toHaveCount(0);
});

test("uncertain creation is not automatically retried", async ({ page }) => {
  const posts = await setup(page, { uncertain: true });
  await page.getByRole("button", { name: "Add Patient", exact: true }).click();
  await page.getByRole("button", { name: "+ Add Synthetic Demo Patient", exact: true }).click();
  await page.getByLabel("Synthetic patient identifier").fill(demo.identifier);
  await page.getByRole("button", { name: "Create Patient", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Patient creation was not confirmed" })).toContainText("No automatic retry");
  expect(posts()).toBe(1);
  await expect(patients(page)).toHaveCount(0);
});

for (const action of ["sign-out", "context-change"]) test(`pending creation cannot restore context after ${action}`, async ({ page }) => {
  let release!: () => void;
  const hold = new Promise<void>(resolve => { release = resolve; });
  const posts = await setup(page, { hold, initial: action === "context-change" });
  if (action === "context-change") await selectPatient(page, existing);
  await page.getByRole("button", { name: "Add Patient", exact: true }).click();
  await page.getByRole("button", { name: "+ Add Synthetic Demo Patient", exact: true }).click();
  await page.getByLabel("Synthetic patient identifier").fill(demo.identifier);
  await page.getByRole("button", { name: "Create Patient", exact: true }).evaluate(button => { (button as HTMLButtonElement).click(); (button as HTMLButtonElement).click(); });
  await expect.poll(posts).toBe(1);
  if (action === "sign-out") await page.getByRole("button", { name: "Sign out", exact: true }).click();
  else await selectPatient(page, existing);
  release();
  if (action === "sign-out") await expect(page.getByLabel("Account identity")).toBeVisible();
  else await selected(page, existing);
  if (action === "sign-out") await expect(page.getByLabel("Clinical note")).toHaveCount(0);
  else await expect(page.getByLabel("Synthetic patient identifier")).toHaveCount(0);
  expect(posts()).toBe(1);
});
