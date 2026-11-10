import { test, expect, Page } from "@playwright/test";
import type { AcceptedFact, Fact } from "../src/components/clinical-types";

const patient = "11111111-1111-4111-8111-111111111111";
const other = "22222222-2222-4222-8222-222222222222";
const actor = "33333333-3333-4333-8333-333333333333";
const practitioner = "44444444-4444-4444-8444-444444444444";
const uid = (n: number) => `90000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const lab: Extract<Fact, { kind: "laboratory" }> = { kind: "laboratory", code: "hba1c", value: "7.1000000000", unit: "%" };
function root(n = 1, fact: Fact = lab): AcceptedFact {
  return { fact_id: uid(n), version_id: uid(n), version: 1, previous_version_id: null,
    patient_id: patient, author_id: actor, author_role: "patient", attribution: "patient_reported",
    fact, observed_at: "2026-01-02T10:00:00+00:00", entered_at: "2026-01-03T10:00:00Z",
    provenance: { source: "individually confirmed extraction", reference: "message:synthetic/confirmation:synthetic", data_origin: "synthetic" },
    entry_method: "LLM_EXTRACTED", status: "CONFIRMED", correction_reason: null,
    confirmed_by_id: actor, confirmed_at: "2026-01-03T09:59:00Z" };
}
function correction(previous: AcceptedFact, n: number, version: number): AcceptedFact {
  return { ...previous, version_id: uid(n), version, previous_version_id: previous.version_id,
    fact: { ...lab, value: "7.2000000000" }, observed_at: "2026-01-01T10:00:00Z",
    entered_at: "2026-01-04T10:00:00Z", entry_method: "STRUCTURED", confirmed_by_id: null,
    confirmed_at: null, correction_reason: "Corrected synthetic transcription",
    provenance: { source: "structured synthetic correction", reference: null, data_origin: "synthetic" } };
}
const base = root();
const second = correction(base, 2, 3);
const current = correction(second, 3, 5);
const chain = [base, second, current];
const accepted = (page: Page) => page.getByRole("article", { name: "Accepted fact", exact: true });
const versions = (page: Page) => page.getByRole("article", { name: "Clinical fact version", exact: true });
const openHistory = (page: Page) => page.getByRole("button", { name: "View details/history", exact: true }).first().click();

async function setup(page: Page, options: { facts?: unknown; history?: unknown; role?: "patient" | "practitioner" } = {}) {
  const role = options.role ?? "patient";
  const state = { facts: options.facts ?? [base], history: options.history ?? [base], requests: [] as { path: string; method: string }[] };
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    state.requests.push({ path, method: route.request().method() });
    if (path.endsWith("/auth/login")) return route.fulfill({ json: { access_token: "synthetic-history-token", token_type: "bearer" } });
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { id: actor, role } });
    if (path.endsWith("/auth/profile")) return route.fulfill({ json: { role, profile_id: role === "patient" ? patient : practitioner } });
    if (path === "/api/v1/patients") return route.fulfill({ json: [{ patient_id: patient }, { patient_id: other }] });
    if (path.endsWith("/patients/me/consents")) return route.fulfill({ json: [] });
    if (path.endsWith("/confirmations")) return route.fulfill({ json: [] });
    if (path.endsWith("/facts")) return route.fulfill({ json: path.includes(other) ? [] : state.facts });
    if (path.endsWith("/history")) {
      expect(route.request().method()).toBe("GET");
      expect(route.request().headers().authorization).toBe("Bearer synthetic-history-token");
      return route.fulfill({ json: state.history });
    }
    if (path.endsWith("/verified-records")) {
      const claim = route.request().postDataJSON().request.draft.claims[0].claim_id;
      return route.fulfill({ json: { representation_version: "record-response-json-v1", records: [{ claim_id: claim, text: "EARLIER SERVER RESTATEMENT" }] } });
    }
    return route.fulfill({ status: 404, json: { detail: "PRIVATE ERROR CONTENT" } });
  });
  await page.goto("/");
  await page.getByLabel("Account identity").fill("history@synthetic.invalid");
  await page.getByLabel("Password", { exact: true }).fill("synthetic-history-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  if (role === "practitioner") await page.getByLabel("Select authorized patient").selectOption(patient);
  return state;
}

test("all T2DM payload types, exact decimals and separate accepted-record section", async ({ page }) => {
  const facts = [base,
    root(10, { kind: "diagnosis", code: "type_2_diabetes", description: "Recorded synthetic diagnosis" }),
    root(11, { kind: "medication", medication_name: "Synthetic recorded medication", dose_value: "0.1000000000", dose_unit: "mg", frequency: "Recorded frequency" }),
    root(12, { kind: "symptom", code: "fatigue", description: "Synthetic reported symptom" }),
    root(13, { kind: "history", category: "allergy", description: "Synthetic reported history" }),
    root(14, { kind: "laboratory", code: "glucose", value: "1E-10", unit: "mmol/L" }),
  ];
  const state = await setup(page, { facts });
  await expect(accepted(page)).toHaveCount(6);
  const panel = page.getByRole("region", { name: "Clinical History", exact: true });
  for (const text of ["7.1000000000", "0.1000000000", "1E-10", "Recorded synthetic diagnosis", "Synthetic recorded medication", "Synthetic reported symptom", "Synthetic reported history"]) {
    await expect(panel.getByText(text, { exact: true })).toBeVisible();
  }
  await expect(page.getByRole("region", { name: "Verified Records", exact: true })).toBeVisible();
  expect(state.requests.filter(r => r.path.endsWith("/history"))).toHaveLength(0);
  await expect(panel.getByRole("button", { name: /correct|save|delete/i })).toHaveCount(0);
});

for (const role of ["patient", "practitioner"] as const) test(`${role} full provenance and accepted correction chain`, async ({ page }) => {
  const rows = role === "patient" ? chain : chain.map(row => ({ ...row, author_role: "practitioner" as const, attribution: "practitioner_managed" as const }));
  const state = await setup(page, { role, facts: [rows[2]], history: rows });
  await expect(accepted(page)).toHaveCount(1);
  await openHistory(page);
  await expect(versions(page)).toHaveCount(3);
  expect(await versions(page).getByRole("heading").allTextContents()).toEqual([
    "Version 1 — Superseded", "Version 3 — Superseded", "Version 5 — Current accepted version",
  ]);
  await expect(versions(page).first()).toContainText(base.observed_at);
  await expect(versions(page).last()).toContainText(current.observed_at); // Retrospective, not reordered.
  for (const text of [patient, base.fact_id, current.version_id, second.version_id, current.entered_at, actor,
    "STRUCTURED", "structured synthetic correction", "synthetic", "Corrected synthetic transcription"]) {
    await expect(versions(page).last()).toContainText(text);
  }
  await expect(versions(page).first()).toContainText(base.provenance.reference!);
  await expect(versions(page).first()).toContainText(base.confirmed_at!);
  await expect(versions(page).first()).toContainText("Confirmed by account ID");
  await expect(versions(page).last()).toContainText("Accepted structured entry");
  expect(state.requests.filter(r => r.path.endsWith("/history"))).toEqual([
    { method: "GET", path: `/api/v1/patients/${patient}/facts/${base.fact_id}/history` },
  ]);
  await page.getByRole("button", { name: "Close details/history", exact: true }).click();
  await expect(versions(page)).toHaveCount(0);
});

test("provenance is escaped text, never an external or private-transcript fetch", async ({ page }) => {
  const fact = { ...base, provenance: { ...base.provenance, reference: "https://private.invalid/<script>not executed</script>" } };
  const state = await setup(page, { facts: [fact], history: [fact] });
  await openHistory(page);
  await expect(versions(page)).toContainText(fact.provenance.reference);
  await expect(versions(page).getByRole("link")).toHaveCount(0);
  expect(state.requests.some(r => /\/messages\/|\/transcripts\/|\/corrections$/.test(r.path))).toBe(false);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length, document.cookie])).toEqual([0, 0, ""]);
});

test("empty state", async ({ page }) => {
  await setup(page, { facts: [] });
  await expect(page.getByText("No accepted facts loaded.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "View details/history", exact: true })).toHaveCount(0);
});

test("Unicode text uses backend code-point limits without truncation", async ({ page }) => {
  const description = "🧪".repeat(1500);
  const fact = { ...root(20, { kind: "history", category: "other", description }),
    provenance: { source: "🧬".repeat(150), reference: "📌".repeat(1500), data_origin: "synthetic" as const } };
  await setup(page, { facts: [fact], history: [fact] });
  await expect(accepted(page)).toHaveCount(1);
  await openHistory(page);
  await expect(versions(page)).toHaveCount(1);
  expect(await versions(page).textContent().then(text => text?.includes(description) && text.includes(fact.provenance.reference))).toBe(true);
});

const malformed: [string, () => unknown][] = [
  ["pending", () => ({ ...base, status: "PENDING" })],
  ["rejected", () => ({ ...base, status: "REJECTED" })],
  ["foreign patient", () => ({ ...base, patient_id: other })],
  ["missing metadata", () => ({ ...base, provenance: undefined })],
  ["root mismatch", () => ({ ...base, fact_id: uid(90) })],
  ["numeric measurement", () => ({ ...base, fact: { ...lab, value: 7.1 } })],
  ["nonfinite measurement", () => ({ ...base, fact: { ...lab, value: "NaN" } })],
  ["excess precision", () => ({ ...base, fact: { ...lab, value: "7.10000000000000001" } })],
  ["incompatible unit", () => ({ ...base, fact: { ...lab, unit: "mg/dL" } })],
  ["naive timestamp", () => ({ ...base, observed_at: "2026-01-02T10:00:00" })],
  ["invalid calendar", () => ({ ...base, observed_at: "2026-02-30T10:00:00Z" })],
  ["attribution mismatch", () => ({ ...base, author_role: "practitioner" })],
  ["wrong confirmer", () => ({ ...base, confirmed_by_id: other })],
  ["unsupported payload", () => ({ ...base, fact: { kind: "advice", text: "DO NOT DISPLAY" } })],
  ["unexpected field", () => ({ ...base, raw_transcript: "DO NOT DISPLAY" })],
];
for (const [label, value] of malformed) test(`malformed current state: ${label}`, async ({ page }) => {
  await setup(page, { facts: [value()] });
  await expect(page.getByRole("alert", { name: "Request error", exact: true })).toBeVisible();
  await expect(accepted(page)).toHaveCount(0);
  await expect(page.getByText("DO NOT DISPLAY", { exact: true })).toHaveCount(0);
});

const badHistory: [string, () => unknown][] = [
  ["empty", () => []],
  ["foreign patient", () => chain.map(row => ({ ...row, patient_id: other }))],
  ["foreign root", () => [root(90)]],
  ["pending", () => [base, second, { ...current, status: "PENDING" }]],
  ["rejected", () => [base, second, { ...current, status: "REJECTED" }]],
  ["unordered", () => [base, current, second]],
  ["broken parent", () => [base, second, { ...current, previous_version_id: base.version_id }]],
  ["duplicate", () => [base, second, second]],
  ["modified current", () => [base, second, { ...current, provenance: { ...current.provenance, source: "DO NOT DISPLAY" } }]],
];
for (const [label, value] of badHistory) test(`history fails closed: ${label}`, async ({ page }) => {
  const state = await setup(page, { facts: [current], history: value() });
  await openHistory(page);
  await expect(page.getByRole("alert", { name: "Clinical history error", exact: true })).toBeVisible();
  await expect(versions(page)).toHaveCount(0);
  await expect(page.getByText("DO NOT DISPLAY", { exact: true })).toHaveCount(0);
  expect(state.requests.filter(r => r.path.endsWith("/history"))).toHaveLength(1);
});

for (const status of [401, 403, 404, 409, 422, 503]) test(`history response ${status} never exposes error content`, async ({ page }) => {
  await setup(page);
  await page.route("**/history", route => route.fulfill({ status, json: { detail: "DO NOT DISPLAY", records: chain } }));
  await openHistory(page);
  await expect(page.getByRole("alert", { name: /^(Request error|Clinical history error)$/ })).toBeVisible();
  await expect(versions(page)).toHaveCount(0);
  await expect(page.getByText("DO NOT DISPLAY", { exact: true })).toHaveCount(0);
  if ([401, 403, 404].includes(status)) await expect(accepted(page)).toHaveCount(0);
  if (status === 401) await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
});

test("newer history refreshes accepted state and clears old verified text without auto-opening history", async ({ page }) => {
  const state = await setup(page);
  await page.getByRole("button", { name: "Request verified record", exact: true }).click();
  await expect(page.getByRole("article", { name: "Server-released record", exact: true })).toHaveCount(1);
  await page.route("**/history", route => {
    state.requests.push({ path: route.request().url(), method: "GET" });
    state.facts = [current]; state.history = chain;
    return route.fulfill({ json: chain });
  });
  await openHistory(page);
  await expect(page.getByRole("status").filter({ hasText: "Clinical record changed" })).toBeVisible();
  await expect(accepted(page)).toContainText("Current accepted version 5");
  await expect(page.getByRole("article", { name: "Server-released record", exact: true })).toHaveCount(0);
  await expect(versions(page)).toHaveCount(0);
  expect(state.requests.filter(r => r.path.endsWith("/history"))).toHaveLength(1);
});

for (const action of ["switch", "sign-out", "refresh"] as const) test(`pending history cannot repopulate after ${action}`, async ({ page }) => {
  await setup(page, { role: "practitioner" });
  let entered!: () => void, release!: () => void, finished!: () => void;
  const started = new Promise<void>(resolve => { entered = resolve; });
  const gate = new Promise<void>(resolve => { release = resolve; });
  const completed = new Promise<void>(resolve => { finished = resolve; });
  await page.route("**/history", async route => {
    entered(); await gate;
    try { await route.fulfill({ json: [base] }); } catch { /* Cancellation is expected. */ }
    finally { finished(); }
  });
  await openHistory(page); await started;
  await expect(page.getByText("Loading clinical history...", { exact: true })).toBeVisible();
  if (action === "switch") {
    await page.getByLabel("Select authorized patient").selectOption(other);
    await expect(page.getByText(`Patient context: ${other}`, { exact: true })).toBeVisible();
  } else if (action === "sign-out") {
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
  } else {
    await page.getByRole("button", { name: "Refresh server state", exact: true }).click();
    await expect(accepted(page)).toHaveCount(1);
  }
  release(); await completed;
  await expect(versions(page)).toHaveCount(0);
  if (action !== "refresh") await expect(accepted(page)).toHaveCount(0);
});
