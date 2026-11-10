import { test, expect, Page } from "@playwright/test";
import type { Account, AcceptedFact, CorrectionRequest } from "../src/components/clinical-types";

const patient = "11111111-1111-4111-8111-111111111111";
const other = "22222222-2222-4222-8222-222222222222";
const actor = "33333333-3333-4333-8333-333333333333";
const profile = "44444444-4444-4444-8444-444444444444";
const originalDoctor = "55555555-5555-4555-8555-555555555555";
const uid = (n: number) => `90000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
function root(n: number, role: Account["role"]): AcceptedFact {
  return { fact_id: uid(n), version_id: uid(n), version: 1, previous_version_id: null,
    patient_id: patient, author_id: role === "patient" ? actor : originalDoctor, author_role: role,
    attribution: role === "patient" ? "patient_reported" : "practitioner_managed",
    fact: { kind: "laboratory", code: "hba1c", value: "7.1000000000", unit: "%" },
    observed_at: "2026-01-01T10:00:00Z", entered_at: "2026-01-02T10:00:00Z",
    provenance: { source: "synthetic fixture", reference: null, data_origin: "synthetic" },
    status: "CONFIRMED", entry_method: "STRUCTURED", confirmed_at: null, confirmed_by_id: null, correction_reason: null };
}
type Outcome = "saved" | "conflict" | "lost" | "lost-committed" | "malformed" | "denied" | "validation";
async function setup(page: Page, role: Account["role"] = "patient") {
  const first = root(1, "patient"), second = root(2, "practitioner");
  const state = { chains: [[first], [second]], posts: [] as CorrectionRequest[], paths: [] as string[],
    reads: 0, outcome: "saved" as Outcome, revoked: false };
  function append(index: number, payload: CorrectionRequest) {
    const old = state.chains[index].at(-1)!;
    const row: AcceptedFact = { ...old, fact: payload.fact, observed_at: payload.observed_at, provenance: payload.provenance,
      correction_reason: payload.correction_reason, fact_id: old.fact_id, version_id: uid(10 + state.posts.length), version: old.version + 2,
      previous_version_id: old.version_id, entered_at: "2026-01-04T10:00:00Z", author_id: actor, author_role: role,
      entry_method: "STRUCTURED", confirmed_at: null, confirmed_by_id: null };
    state.chains[index].push(row); return row;
  }
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    state.paths.push(path);
    if (path.endsWith("/auth/login")) return route.fulfill({ json: { access_token: "synthetic-correction-token", token_type: "bearer" } });
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { id: actor, role } });
    if (path.endsWith("/auth/profile")) return route.fulfill({ json: { role, profile_id: role === "patient" ? patient : profile } });
    if (path === "/api/v1/patients") return route.fulfill({ json: state.revoked ? [] : [{ patient_id: patient }, { patient_id: other }] });
    if (path.endsWith("/patients/me/consents")) return route.fulfill({ json: [] });
    if (state.revoked && path.includes(`/patients/${patient}/`)) return route.fulfill({ status: 403, json: { detail: "PRIVATE DENIAL" } });
    if (path.endsWith("/confirmations")) return route.fulfill({ json: [] });
    if (path.endsWith("/facts")) return route.fulfill({ json: path.includes(other) ? [] : state.chains.map(chain => chain.at(-1)) });
    const index = path.includes(uid(2)) ? 1 : 0;
    if (path.endsWith("/history")) { state.reads++; return route.fulfill({ json: state.chains[index] }); }
    if (path.endsWith("/corrections")) {
      const payload = route.request().postDataJSON() as CorrectionRequest;
      state.posts.push(payload);
      expect(Object.keys(payload).sort()).toEqual(["correction_reason", "expected_version_id", "fact", "observed_at", "provenance"]);
      expect(route.request().headers().authorization).toBe("Bearer synthetic-correction-token");
      if (state.outcome === "denied") { state.revoked = true; return route.fulfill({ status: 403, json: { detail: "PRIVATE DENIAL" } }); }
      if (state.outcome === "validation") return route.fulfill({ status: 422, json: { detail: "PRIVATE VALIDATION" } });
      if (state.outcome === "lost") return route.abort("failed");
      if (state.outcome === "conflict") {
        append(index, { ...payload, correction_reason: "Concurrent synthetic correction" });
        return route.fulfill({ status: 409, json: { detail: "PRIVATE CONFLICT" } });
      }
      const row = append(index, payload);
      if (state.outcome === "lost-committed") return route.abort("failed");
      if (state.outcome === "malformed") return route.fulfill({ status: 201, json: { ...row, patient_id: other, provenance: { source: "DO NOT DISPLAY" } } });
      return route.fulfill({ status: 201, json: row });
    }
    if (path.endsWith("/verified-records")) {
      const claim = route.request().postDataJSON().request.draft.claims[0].claim_id;
      return route.fulfill({ json: { representation_version: "record-response-json-v1", records: [{ claim_id: claim, text: "PRIOR VERIFIED RESTATEMENT" }] } });
    }
    return route.fulfill({ status: 404, json: { detail: "PRIVATE ERROR" } });
  });
  await page.goto("/");
  await page.getByLabel("Account identity").fill("corrections@synthetic.invalid");
  await page.getByLabel("Password", { exact: true }).fill("synthetic-correction-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  if (role === "practitioner") await page.getByLabel("Select authorized patient").selectOption(patient);
  await expect(page.getByRole("article", { name: "Accepted fact", exact: true })).toHaveCount(2);
  return state;
}
const form = (page: Page) => page.getByRole("region", { name: "Structured correction", exact: true });
async function edit(page: Page) {
  await page.getByRole("button", { name: "Correct record", exact: true }).click();
  await expect(form(page)).toBeVisible();
  await form(page).getByLabel("Correction reason", { exact: true }).fill("Correct synthetic recorded information");
}
async function review(page: Page) {
  await form(page).getByRole("button", { name: "Review correction", exact: true }).click();
  await expect(page.getByRole("region", { name: "Review new version", exact: true })).toBeVisible();
}
async function submit(page: Page) { await page.getByRole("button", { name: "Submit new version", exact: true }).click(); }

for (const role of ["patient", "practitioner"] as const) test(`${role} category permission, immutable history and verified invalidation`, async ({ page }) => {
  const state = await setup(page, role);
  const cards = page.getByRole("article", { name: "Accepted fact", exact: true });
  const eligible = cards.nth(role === "patient" ? 0 : 1), ineligible = cards.nth(role === "patient" ? 1 : 0);
  await expect(eligible.getByRole("button", { name: "Correct record", exact: true })).toHaveCount(1);
  await expect(ineligible.getByRole("button", { name: "Correct record", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Request verified record", exact: true }).click();
  await expect(page.getByRole("article", { name: "Server-released record", exact: true })).toHaveCount(1);
  await edit(page);
  expect(state.reads).toBeGreaterThan(0);
  await form(page).getByLabel("Laboratory value", { exact: true }).fill("7.2000000000");
  await form(page).getByLabel("Provenance source", { exact: true }).fill("explicit synthetic correction");
  await review(page);
  expect(state.posts).toHaveLength(0);
  const expected = state.chains[role === "patient" ? 0 : 1][0].version_id;
  await expect(form(page).getByLabel("Expected current version ID")).toHaveValue(expected);
  await submit(page);
  await expect(page.getByRole("status").filter({ hasText: "Correction acknowledged" })).toBeVisible();
  await expect(page.getByRole("article", { name: "Clinical fact version", exact: true })).toHaveCount(2);
  expect(state.posts).toHaveLength(1);
  expect(state.posts[0].expected_version_id).toBe(expected);
  expect(state.posts[0].fact).toMatchObject({ value: "7.2000000000" });
  await expect(page.getByText("PRIOR VERIFIED RESTATEMENT", { exact: true })).toHaveCount(0);
  const history = page.getByRole("region", { name: "Accepted correction history", exact: true });
  await expect(history).toContainText("Version 1 — Superseded");
  await expect(history).toContainText("Version 3 — Current accepted version");
  if (role === "practitioner") await expect(history).toContainText(originalDoctor); // Not original-author-only.
});

for (const kind of ["laboratory", "diagnosis", "medication", "symptom", "history"] as const) test(`replacement payload ${kind}, including cross-kind/code`, async ({ page }) => {
  const state = await setup(page);
  await edit(page);
  await form(page).getByLabel("Replacement fact type").selectOption(kind);
  if (kind === "laboratory") {
    await form(page).getByLabel("Laboratory code").selectOption("glucose");
    await form(page).getByLabel("Laboratory value", { exact: true }).fill("100.0000000000");
    await form(page).getByLabel("Laboratory unit").fill("mg/dL");
  } else if (kind === "diagnosis") await form(page).getByLabel("Diagnosis description (optional)").fill("Corrected recorded diagnosis description");
  else if (kind === "medication") {
    await form(page).getByLabel("Recorded medication name").fill("Synthetic recorded medication");
    await form(page).getByLabel("Recorded dose value (optional)").fill("0.1000000000");
    await form(page).getByLabel("Recorded dose unit (optional)").fill("mg");
    await form(page).getByLabel("Recorded frequency (optional)").fill("Recorded schedule");
  } else if (kind === "symptom") {
    await form(page).getByLabel("Symptom code").selectOption("fatigue");
    await form(page).getByLabel("Symptom description").fill("Corrected synthetic symptom");
  } else {
    await form(page).getByLabel("History category").selectOption("allergy");
    await form(page).getByLabel("History description").fill("Corrected synthetic history");
  }
  await form(page).getByLabel("Observed at (ISO timestamp with timezone)").fill("2025-12-30T12:30:00+05:30");
  await form(page).getByLabel("Provenance reference (optional)").fill("source:synthetic-reference");
  await review(page); await submit(page);
  await expect(page.getByRole("status").filter({ hasText: "Correction acknowledged" })).toBeVisible();
  expect(state.posts[0].fact.kind).toBe(kind);
  expect(state.posts[0].observed_at).toBe("2025-12-30T12:30:00+05:30");
  expect(state.posts[0].provenance.reference).toBe("source:synthetic-reference");
  if (kind === "laboratory") expect(state.posts[0].fact).toMatchObject({ code: "glucose", value: "100.0000000000" });
  if (kind === "medication") expect(state.posts[0].fact).toMatchObject({ dose_value: "0.1000000000" });
});

for (const invalid of ["precision", "timezone", "reason", "dose"] as const) test(`invalid ${invalid} is withheld before submission`, async ({ page }) => {
  const state = await setup(page); await edit(page);
  if (invalid === "precision") await form(page).getByLabel("Laboratory value", { exact: true }).fill("7.10000000000000001");
  if (invalid === "timezone") await form(page).getByLabel("Observed at (ISO timestamp with timezone)").fill("2026-01-01T10:00:00");
  if (invalid === "reason") await form(page).getByLabel("Correction reason", { exact: true }).fill("   ");
  if (invalid === "dose") {
    await form(page).getByLabel("Replacement fact type").selectOption("medication");
    await form(page).getByLabel("Recorded medication name").fill("Synthetic medication");
    await form(page).getByLabel("Recorded dose value (optional)").fill("0.1");
  }
  await form(page).getByRole("button", { name: "Review correction", exact: true }).click();
  await expect(page.getByRole("alert", { name: "Correction validation error", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Submit new version", exact: true })).toHaveCount(0);
  expect(state.posts).toHaveLength(0);
});

test("editing reviewed fields requires another explicit review", async ({ page }) => {
  const state = await setup(page); await edit(page); await review(page);
  await form(page).getByLabel("Laboratory value", { exact: true }).fill("7.3000000000");
  await expect(page.getByRole("button", { name: "Submit new version", exact: true })).toHaveCount(0);
  expect(state.posts).toHaveLength(0);
  await review(page); await submit(page);
  await expect(page.getByRole("status").filter({ hasText: "Correction acknowledged" })).toBeVisible();
});

test("409 reloads current version and never resubmits without fresh review", async ({ page }) => {
  const state = await setup(page); state.outcome = "conflict";
  await edit(page); await review(page); await submit(page);
  await expect(page.getByRole("status").filter({ hasText: "stale (409)" })).toBeVisible();
  await expect(page.getByRole("article", { name: "Clinical fact version", exact: true })).toHaveCount(2);
  expect(state.posts).toHaveLength(1);
  state.outcome = "saved"; const reads = state.reads;
  await edit(page);
  expect(state.reads).toBeGreaterThan(reads);
  await expect(form(page).getByLabel("Expected current version ID")).toHaveValue(state.chains[0].at(-1)!.version_id);
  await review(page); await submit(page);
  await expect(page.getByRole("status").filter({ hasText: "Correction acknowledged" })).toBeVisible();
  expect(state.posts).toHaveLength(2);
  expect(state.posts[0].expected_version_id).not.toBe(state.posts[1].expected_version_id);
});

for (const outcome of ["lost", "lost-committed", "malformed", "validation"] as const) test(`${outcome} acknowledgement requires fresh check and no automatic write retry`, async ({ page }) => {
  const state = await setup(page); state.outcome = outcome;
  await edit(page); await review(page); await submit(page);
  await expect(page.getByRole("status").filter({ hasText: outcome === "validation" ? "values were rejected" : "acknowledgement is uncertain" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Accepted correction history", exact: true })).toBeVisible();
  expect(state.posts).toHaveLength(1);
  await expect(page.getByText("DO NOT DISPLAY", { exact: true })).toHaveCount(0);
  await expect(form(page)).toHaveCount(0);
  const reads = state.reads;
  await edit(page);
  expect(state.reads).toBeGreaterThan(reads);
  expect(state.posts).toHaveLength(1);
});

test("timed-out acknowledgement requires fresh history and no automatic write retry", async ({ page }) => {
  const state = await setup(page); await edit(page); await review(page);
  await page.clock.install();
  let entered!: () => void, release!: () => void;
  const started = new Promise<void>(resolve => { entered = resolve; });
  const gate = new Promise<void>(resolve => { release = resolve; });
  let posts = 0;
  await page.route("**/corrections", async route => {
    posts++; entered(); await gate;
    try { await route.fulfill({ status: 503, json: { detail: "PRIVATE TIMEOUT" } }); } catch { /* Timed-out request. */ }
  });
  try {
    await submit(page); await started;
    await page.clock.fastForward(20001);
    await expect(page.getByRole("status").filter({ hasText: "acknowledgement is uncertain" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Accepted correction history", exact: true })).toBeVisible();
    const reads = state.reads;
    await edit(page);
    expect(state.reads).toBeGreaterThan(reads);
    expect(posts).toBe(1);
  } finally { release(); }
});

test("revocation during correction denies access and clears protected state", async ({ page }) => {
  const state = await setup(page, "practitioner"); state.outcome = "denied";
  await edit(page); await review(page); await submit(page);
  await expect(page.getByRole("alert", { name: "Request error", exact: true })).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(0);
  await expect(page.getByText("PRIVATE DENIAL", { exact: true })).toHaveCount(0);
  expect(state.posts).toHaveLength(1);
});

for (const denial of ["revoked", "cross-patient"] as const) test(`${denial} fresh history prevents opening correction form`, async ({ page }) => {
  const state = await setup(page);
  await page.route("**/history", route => route.fulfill(denial === "revoked"
    ? { status: 403, json: { detail: "PRIVATE DENIAL" } }
    : { json: [{ ...state.chains[0][0], patient_id: other }] }));
  await page.getByRole("button", { name: "Correct record", exact: true }).click();
  await expect(page.getByRole("alert", { name: denial === "revoked" ? "Request error" : "Clinical history error", exact: true })).toBeVisible();
  await expect(form(page)).toHaveCount(0);
  expect(state.posts).toHaveLength(0);
});

for (const action of ["duplicate", "switch", "sign-out"] as const) test(`pending correction: ${action}`, async ({ page }) => {
  const state = await setup(page, "practitioner");
  let entered!: () => void, release!: () => void, finished!: () => void;
  const started = new Promise<void>(resolve => { entered = resolve; });
  const gate = new Promise<void>(resolve => { release = resolve; });
  const completed = new Promise<void>(resolve => { finished = resolve; });
  let posts = 0;
  await page.route("**/corrections", async route => {
    posts++; entered(); await gate;
    try { await route.fulfill({ status: 503, json: { detail: "PRIVATE UNCERTAIN" } }); } catch { /* Context cancellation expected. */ }
    finally { finished(); }
  });
  await edit(page); await review(page);
  await page.getByRole("button", { name: "Submit new version", exact: true }).evaluate(button => { (button as HTMLButtonElement).click(); (button as HTMLButtonElement).click(); });
  await started; expect(posts).toBe(1);
  if (action === "switch") {
    await page.getByLabel("Select authorized patient").selectOption(other);
    await expect(page.getByText(`Patient context: ${other}`, { exact: true })).toBeVisible();
  } else if (action === "sign-out") {
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
  }
  release(); await completed;
  if (action === "duplicate") await expect(page.getByRole("status").filter({ hasText: "acknowledgement is uncertain" })).toBeVisible();
  else {
    await expect(form(page)).toHaveCount(0);
    await expect(page.getByText(/acknowledgement is uncertain/)).toHaveCount(0);
    await expect(page.getByRole("article", { name: "Accepted fact", exact: true })).toHaveCount(0);
  }
  expect(posts).toBe(1);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length, document.cookie])).toEqual([0, 0, ""]);
});
