import { test, expect, Page } from "@playwright/test";
import { createHash, randomUUID } from "node:crypto";

const patient = "11111111-1111-4111-8111-111111111111";
const other = "22222222-2222-4222-8222-222222222222";
const speaker = "33333333-3333-4333-8333-333333333333";
const source = "44444444-4444-4444-8444-444444444444";
const quoteA = "HbA1c: 7.1 % at 2026-01-01T10:00:00Z";
const quoteB = "HbA1c: 8.2 % at 2026-01-02T10:00:00Z";
const sourceText = `${quoteA}\n${quoteB}`;
function item(id: string) {
  const first = id === "a";
  return { confirmation_id: first ? "55555555-5555-4555-8555-555555555555" : "66666666-6666-4666-8666-666666666666", patient_id: patient, source_message_id: source,
    speaker_id: speaker, speaker_role: "patient", attribution: "patient_reported",
    proposed_fact: { kind: "laboratory", code: "hba1c", value: id === "a" ? "7.1" : "8.2", unit: "%" },
    observed_at: first ? "2026-01-01T10:00:00Z" : "2026-01-02T10:00:00Z", source_quote: first ? quoteA : quoteB,
    source_start: first ? 0 : quoteA.length + 1, source_end: first ? quoteA.length : sourceText.length,
    provider: { name: "mock", version: "v1" }, grounding_version: "source-grounding-v1",
    source_sha256: createHash("sha256").update(sourceText).digest("hex"), data_origin: "synthetic",
    created_at: "2026-01-03T10:00:00Z", decided_at: null as string | null,
    accepted_version_id: null as string | null, status: "PENDING" };
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
    if (path.endsWith("/auth/login")) return route.fulfill({ json: { access_token: "synthetic-test-token", token_type: "bearer" } });
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { id: speaker, role: options.practitioner ? "practitioner" : "patient" } });
    if (path.endsWith("/auth/profile")) return route.fulfill({ json: { profile_id: options.practitioner ? source : patient, role: options.practitioner ? "practitioner" : "patient" } });
    if (path === "/api/v1/patients") return route.fulfill({ json: [{ patient_id: patient }] });
    if (path.endsWith("/patients/me/consents")) return route.fulfill({ json: [] });
    if (options.denied || path.includes(other)) return route.fulfill({ status: options.denied || 403, json: { detail: "private server detail must never render" } });
    if (path.endsWith("/extractions")) {
      const input = route.request().postDataJSON();
      items.forEach(i => { i.source_message_id = input.source_message_id; });
      return route.fulfill({ status: 201, json: { source_message_id: input.source_message_id, confirmations: items } });
    }
    if (path.endsWith("/confirmations")) return route.fulfill({ json: items });
    if (path.endsWith("/facts")) return route.fulfill({ json: accepted });
    if (path.endsWith("/confirm") || path.endsWith("/reject")) {
      decisions++;
      await new Promise(resolve => setTimeout(resolve, 150));
      const target = items.find(i => path.includes(`/confirmations/${i.confirmation_id}/`))!;
      target.status = options.conflict ? "REJECTED" : path.endsWith("/confirm") ? "CONFIRMED" : "REJECTED";
      target.decided_at = "2026-01-04T10:00:00Z";
      if (target.status === "CONFIRMED") {
        target.accepted_version_id = randomUUID();
        accepted.push({ fact_id: target.accepted_version_id, version_id: target.accepted_version_id, version: 1, previous_version_id: null, patient_id: patient, status: "CONFIRMED", author_id: speaker, author_role: target.speaker_role, fact: target.proposed_fact, attribution: target.attribution, observed_at: target.observed_at,
          entered_at: target.decided_at, correction_reason: null, provenance: { source: "mock-extraction", reference: source, data_origin: "synthetic" }, entry_method: "LLM_EXTRACTED", confirmed_by_id: speaker, confirmed_at: target.decided_at });
      }
      return route.fulfill({ status: options.conflict ? 409 : 200, json: target });
    }
    return route.fulfill({ status: 404, json: {} });
  });
  await page.goto("/");
  await page.getByLabel("Account identity").fill("synthetic@example.test");
  await page.getByLabel("Password", { exact: true }).fill("synthetic-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  if (options.practitioner) await page.getByLabel("Select authorized patient").selectOption(patient);
  return { decisions: () => decisions };
}

test("individual cards, independent decisions and accepted-state boundary", async ({ page }) => {
  await setup(page);
  const cards = page.getByRole("article", { name: "Fact confirmation", exact: true });
  await expect(cards).toHaveCount(2);
  await expect(cards.first()).toContainText(quoteA);
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
  await expect(page.getByLabel("Select authorized patient").locator(`option[value="${other}"]`)).toHaveCount(0);
  await page.route(`**/patients/${patient}/facts`, route => route.fulfill({ status: 403, json: {} }));
  await page.getByLabel("Select authorized patient").selectOption(patient);
  await expect(page.getByRole("alert", { name: "Request error" })).toContainText("access denied");
  await expect(page.getByRole("article")).toHaveCount(0);
});

test("synthetic source message displays cards in chat and sign out clears them", async ({ page }) => {
  await setup(page);
  await page.getByLabel("Synthetic message").fill(sourceText);
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
  let completed = false;
  await page.route("**/extractions", async route => {
    entered(); await gate;
    const sourceId = route.request().postDataJSON().source_message_id;
    await route.fulfill({ status: 201, json: { source_message_id: sourceId, confirmations: [{ ...item("a"), source_message_id: sourceId }] } }).catch(() => {});
    completed = true;
  });
  await page.getByLabel("Synthetic message").fill("Synthetic text");
  await page.getByRole("button", { name: "Send for extraction" }).click();
  await started;
  await page.getByRole("button", { name: "Sign out" }).click();
  release(); await expect.poll(() => completed).toBe(true);
  await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(0);
});
