import { test, expect, Page } from "@playwright/test";

const patient = "11111111-1111-4111-8111-111111111111";
const other = "22222222-2222-4222-8222-222222222222";
const speaker = "33333333-3333-4333-8333-333333333333";
const source = "44444444-4444-4444-8444-444444444444";
function item(id: string) {
  return { confirmation_id: id, patient_id: patient, source_message_id: source,
    speaker_id: speaker, speaker_role: "patient", attribution: "patient_reported",
    proposed_fact: { kind: "laboratory", code: "hba1c", value: id === "a" ? "7.1" : "8.2", unit: "%" },
    observed_at: "2026-01-01T10:00:00Z", source_quote: `Synthetic source ${id}`, status: "PENDING" };
}
async function setup(page: Page, options: { conflict?: boolean; denied?: number; badScope?: boolean; wrongPatient?: boolean; practitioner?: boolean } = {}) {
  const items = [item("a"), item("b")];
  if (options.practitioner) items.forEach(i => { i.speaker_role = "practitioner"; i.attribution = "practitioner_managed"; });
  if (options.badScope) items[0].speaker_id = other;
  if (options.wrongPatient) items[0].patient_id = other;
  let decisions = 0;
  const accepted: object[] = [];
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/login")) return route.fulfill({ json: { access_token: "synthetic-test-token" } });
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { id: speaker, role: options.practitioner ? "practitioner" : "patient" } });
    if (options.denied || path.includes(other)) return route.fulfill({ status: options.denied || 403, json: { detail: "private server detail must never render" } });
    if (path.endsWith("/extractions")) {
      const input = route.request().postDataJSON();
      items.forEach(i => { i.source_message_id = input.source_message_id; });
      return route.fulfill({ status: 201, json: { confirmations: items } });
    }
    if (path.endsWith("/confirmations")) return route.fulfill({ json: items });
    if (path.endsWith("/facts")) return route.fulfill({ json: accepted });
    if (path.endsWith("/confirm") || path.endsWith("/reject")) {
      decisions++;
      await new Promise(resolve => setTimeout(resolve, 150));
      const target = items.find(i => path.includes(`/confirmations/${i.confirmation_id}/`))!;
      target.status = options.conflict ? "REJECTED" : path.endsWith("/confirm") ? "CONFIRMED" : "REJECTED";
      if (target.status === "CONFIRMED") accepted.push({ version_id: target.confirmation_id, patient_id: patient, status: "CONFIRMED", fact: target.proposed_fact, attribution: target.attribution, observed_at: target.observed_at });
      return route.fulfill({ status: options.conflict ? 409 : 200, json: target });
    }
    return route.fulfill({ status: 404, json: {} });
  });
  await page.goto("/");
  await page.getByLabel("Account identity").fill("synthetic@example.test");
  await page.getByLabel("Password", { exact: true }).fill("synthetic-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByLabel("Patient profile UUID").fill(patient);
  await page.getByRole("button", { name: "Open patient conversation" }).click();
  return { decisions: () => decisions };
}

test("individual cards, independent decisions and accepted-state boundary", async ({ page }) => {
  await setup(page);
  const cards = page.getByRole("article", { name: "Fact confirmation", exact: true });
  await expect(cards).toHaveCount(2);
  await expect(cards.first()).toContainText("Synthetic source a");
  await expect(cards.first()).toContainText("Patient-reported");
  await expect(page.getByRole("article", { name: "Accepted fact", exact: true })).toHaveCount(0);
  await cards.first().getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(cards.first()).toContainText("CONFIRMED");
  await expect(cards.nth(1)).toContainText("PENDING");
  await cards.nth(1).getByRole("button", { name: "Reject", exact: true }).click();
  await expect(cards.nth(1)).toContainText("REJECTED");
  await expect(page.getByRole("article", { name: "Accepted fact", exact: true })).toHaveCount(1);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
});

test("duplicate clicks are guarded and confirmation is not optimistic", async ({ page }) => {
  const state = await setup(page);
  const card = page.getByRole("article", { name: "Fact confirmation", exact: true }).first();
  await card.getByRole("button", { name: "Confirm", exact: true }).evaluate((button: HTMLButtonElement) => { button.click(); button.click(); });
  await expect(card).toContainText("PENDING");
  await expect(card).toContainText("CONFIRMED");
  expect(state.decisions()).toBe(1);
});

test("409 reloads the authoritative decision without adding an accepted fact", async ({ page }) => {
  await setup(page, { conflict: true });
  const card = page.getByRole("article", { name: "Fact confirmation", exact: true }).first();
  await card.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(card).toContainText("REJECTED");
  await expect(page.getByRole("status")).toContainText("Server state has been reloaded");
  await expect(page.getByRole("article", { name: "Accepted fact", exact: true })).toHaveCount(0);
});

for (const status of [401, 403, 404]) test(`authorization ${status} clears content and hides server details`, async ({ page }) => {
  await setup(page, { denied: status });
  await expect(page.getByRole("alert", { name: "Request error" })).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(0);
  await expect(page.getByText("private server detail must never render")).toHaveCount(0);
});

test("mismatched speaker response fails closed", async ({ page }) => {
  await setup(page, { badScope: true });
  await expect(page.getByRole("alert", { name: "Request error" })).toContainText("access denied");
  await expect(page.getByRole("article")).toHaveCount(0);
});

test("practitioner attribution and patient context switching clear conversation", async ({ page }) => {
  await setup(page, { practitioner: true });
  await expect(page.getByRole("article", { name: "Fact confirmation", exact: true }).first()).toContainText("Practitioner-managed");
  await page.getByRole("button", { name: "Change patient context" }).click();
  await expect(page.getByRole("article")).toHaveCount(0);
  await page.getByLabel("Patient profile UUID").fill(other);
  await page.getByRole("button", { name: "Open patient conversation" }).click();
  await expect(page.getByRole("alert", { name: "Request error" })).toContainText("access denied");
  await expect(page.getByRole("article")).toHaveCount(0);
});

test("synthetic source message displays cards in chat and sign out clears them", async ({ page }) => {
  await setup(page);
  await page.getByLabel("Synthetic message").fill("HbA1c: 7.1 % at 2026-01-01T10:00:00Z");
  await page.getByRole("button", { name: "Send for extraction" }).click();
  await expect(page.locator(".source-text")).toContainText("HbA1c: 7.1 %");
  await expect(page.getByRole("article", { name: "Fact confirmation", exact: true })).toHaveCount(2);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("article")).toHaveCount(0);
  await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
});

for (const status of [401, 403, 404]) test(`decision authorization ${status} removes previously displayed content`, async ({ page }) => {
  await setup(page);
  const card = page.getByRole("article", { name: "Fact confirmation", exact: true }).first();
  await expect(card).toBeVisible();
  await page.route("**/confirmations/*/confirm", route => route.fulfill({ status, json: { detail: "secret detail" } }));
  await card.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(page.getByRole("alert", { name: "Request error" })).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(0);
  await expect(page.getByText("secret detail", { exact: true })).toHaveCount(0);
});

test("mismatched patient response fails closed", async ({ page }) => {
  await setup(page, { wrongPatient: true });
  await expect(page.getByRole("alert", { name: "Request error" })).toContainText("access denied");
  await expect(page.getByRole("article")).toHaveCount(0);
});

test("late extraction response cannot restore content after sign out", async ({ page }) => {
  await setup(page);
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let entered!: () => void;
  const started = new Promise<void>(resolve => { entered = resolve; });
  await page.route("**/extractions", async route => {
    entered(); await gate;
    await route.fulfill({ status: 201, json: { confirmations: [item("a")] } });
  });
  await page.getByLabel("Synthetic message").fill("Synthetic text");
  await page.getByRole("button", { name: "Send for extraction" }).click();
  await started;
  await page.getByRole("button", { name: "Sign out" }).click();
  const response = page.waitForResponse(r => r.url().endsWith("/extractions"));
  release(); await response;
  await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(0);
});
