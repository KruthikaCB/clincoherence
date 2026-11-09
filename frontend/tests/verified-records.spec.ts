import { test, expect, Page } from "@playwright/test";

const patient = "11111111-1111-4111-8111-111111111111";
const other = "22222222-2222-4222-8222-222222222222";
const actor = "33333333-3333-4333-8333-333333333333";
const version = "44444444-4444-4444-8444-444444444444";
const fact = { version_id: version, patient_id: patient, status: "CONFIRMED",
  fact_id: "66666666-6666-4666-8666-666666666666", version: 1, previous_version_id: null,
  author_id: actor, author_role: "patient", attribution: "patient_reported",
  observed_at: "2026-01-01T10:00:00Z",
  entered_at: "2026-01-01T11:00:00Z", correction_reason: null,
  provenance: { source: "synthetic-fixture", reference: null, data_origin: "synthetic" },
  entry_method: "STRUCTURED", confirmed_by_id: null, confirmed_at: null,
  fact: { kind: "laboratory", code: "hba1c", value: "7.1000000000", unit: "%" } };
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

async function setup(page: Page, practitioner = false, empty = false) {
  await page.route("**/api/v1/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/login")) return route.fulfill({ json: { access_token: "synthetic-token", token_type: "bearer" } });
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { id: actor, role: practitioner ? "practitioner" : "patient" } });
    if (path.endsWith("/auth/profile")) return route.fulfill({ json: { profile_id: practitioner ? version : patient, role: practitioner ? "practitioner" : "patient" } });
    if (path === "/api/v1/patients") return route.fulfill({ json: [{ patient_id: patient }, { patient_id: other }] });
    if (path.endsWith("/patients/me/consents")) return route.fulfill({ json: [] });
    if (path.endsWith("/confirmations")) return route.fulfill({ json: [] });
    if (path.endsWith("/facts")) return route.fulfill({ json: empty ? [] : [{ ...fact, patient_id: path.includes(other) ? other : patient, fact_id: path.includes(other) ? "77777777-7777-4777-8777-777777777777" : fact.fact_id, version_id: path.includes(other) ? "55555555-5555-4555-8555-555555555555" : version }] });
    return route.fulfill({ status: 404, json: {} });
  });
  await page.goto("/");
  await page.getByLabel("Account identity").fill("synthetic@example.test");
  await page.getByLabel("Password", { exact: true }).fill("synthetic-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  if (practitioner) await page.getByLabel("Select authorized patient").selectOption(patient);
  await expect(page.getByRole("region", { name: "Verified Records", exact: true })).toBeVisible();
}

const requestButton = (page: Page) => page.getByRole("button", { name: "Request verified record", exact: true });
const records = (page: Page) => page.getByRole("article", { name: "Server-released record", exact: true });

for (const practitioner of [false, true]) test(`${practitioner ? "practitioner" : "patient"} exact request and server-only rendering`, async ({ page }) => {
  await setup(page, practitioner);
  const operations: string[] = [];
  await page.route("**/verified-records", route => {
    const body = route.request().postDataJSON();
    expect(route.request().headers().authorization).toBe("Bearer synthetic-token");
    expect(Object.keys(body).sort()).toEqual(["operation_id", "request"]);
    expect(body.operation_id).toMatch(uuid); operations.push(body.operation_id);
    expect(Object.keys(body.request).sort()).toEqual(["bindings", "draft"]);
    expect(body.request.bindings).toEqual([]);
    const draft = body.request.draft;
    expect(draft.draft_id).toMatch(uuid);
    expect(draft.patient_id).toBe(patient);
    expect(draft.claims).toHaveLength(1);
    expect(draft.claims[0]).toEqual({
      claim_id: expect.stringMatching(uuid), patient_id: patient, claim_type: "FACT_RESTATEMENT",
      fact_version_ids: [version], source_attribution: { author_id: actor, author_role: "patient", category: "patient_reported" },
      proposed_content: { fact: fact.fact, observed_at: fact.observed_at },
    });
    return route.fulfill({ json: { representation_version: "record-response-json-v1", records: [{ claim_id: draft.claims[0].claim_id, text: "SERVER-ONLY RESTATEMENT <script>synthetic</script>" }] } });
  });
  await expect(records(page)).toHaveCount(0);
  await expect(page.getByRole("article", { name: "Accepted fact", exact: true })).toHaveCount(1);
  await requestButton(page).click();
  await expect(records(page)).toContainText("SERVER-ONLY RESTATEMENT <script>synthetic</script>");
  await expect(page.getByText(/Technical verification is not medical correctness/)).toBeVisible();
  await requestButton(page).click();
  await expect.poll(() => operations.length).toBe(2);
  expect(new Set(operations).size).toBe(2);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length, document.cookie])).toEqual([0, 0, ""]);
});

for (const status of [401, 403, 404, 409, 422, 503]) test(`withheld ${status} removes old released content and never echoes details`, async ({ page }) => {
  await setup(page);
  let calls = 0;
  await page.route("**/verified-records", route => {
    calls++;
    const claim = route.request().postDataJSON().request.draft.claims[0].claim_id;
    return route.fulfill(calls === 1 ? { json: { representation_version: "record-response-json-v1", records: [{ claim_id: claim, text: "EARLIER RELEASE" }] } }
      : { status, json: { detail: "PRIVATE SERVER DETAIL", records: [{ text: "FORGED ERROR CONTENT" }] } });
  });
  await requestButton(page).click(); await expect(records(page)).toHaveCount(1);
  await requestButton(page).click();
  await expect(page.getByRole("alert", { name: /^(Request error|Verified record error)$/ })).toBeVisible();
  await expect(records(page)).toHaveCount(0);
  await expect(page.getByText(/PRIVATE SERVER DETAIL|FORGED ERROR CONTENT|EARLIER RELEASE/)).toHaveCount(0);
  expect(calls).toBe(2);
  if (status === 401) await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
  if (status === 403 || status === 404) await expect(page.getByRole("article")).toHaveCount(0);
});

test("network failure has no automatic retry and explicit retry has a new operation", async ({ page }) => {
  await setup(page);
  const operations: string[] = [];
  await page.route("**/verified-records", route => { operations.push(route.request().postDataJSON().operation_id); return route.abort("failed"); });
  await requestButton(page).click();
  await expect(page.getByRole("alert", { name: "Verified record error" })).toContainText("No automatic retry");
  await page.waitForTimeout(300);
  expect(operations).toHaveLength(1);
  await requestButton(page).click();
  await expect.poll(() => operations.length).toBe(2);
  expect(operations[0]).not.toBe(operations[1]);
  await expect(records(page)).toHaveCount(0);
});

for (const change of ["sign out", "patient switch"]) test(`late result suppressed after ${change}`, async ({ page }) => {
  await setup(page, change === "patient switch");
  let release!: () => void;
  let entered!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const started = new Promise<void>(resolve => { entered = resolve; });
  let calls = 0;
  await page.route("**/verified-records", async route => {
    calls++; entered(); await gate;
    await route.fulfill({ json: { representation_version: "record-response-json-v1", records: [{ claim_id: route.request().postDataJSON().request.draft.claims[0].claim_id, text: "STALE PRIVATE RELEASE" }] } }).catch(() => {});
  });
  await requestButton(page).evaluate((button: HTMLButtonElement) => { button.click(); button.click(); });
  await started;
  expect(calls).toBe(1);
  await page.getByRole("button", { name: change === "sign out" ? "Sign out" : "Change patient context", exact: true }).click();
  if (change === "patient switch") {
    await page.getByLabel("Select authorized patient").selectOption(other);
  }
  release();
  await expect(page.getByText("STALE PRIVATE RELEASE", { exact: true })).toHaveCount(0);
  await expect(records(page)).toHaveCount(0);
});

test("timeout is uncertain and never auto-retried", async ({ page }) => {
  await setup(page);
  await page.clock.install();
  let calls = 0;
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/verified-records", async route => { calls++; await gate; await route.abort().catch(() => {}); });
  await requestButton(page).click();
  await expect.poll(() => calls).toBe(1);
  await page.clock.fastForward(20001);
  await expect(page.getByRole("alert", { name: "Verified record error" })).toContainText("No automatic retry");
  expect(calls).toBe(1); await expect(records(page)).toHaveCount(0);
  release();
});

for (const malformed of ["wrong claim", "wrong version", "extra content", "truncated"]) test(`invalid released body: ${malformed}`, async ({ page }) => {
  await setup(page);
  await page.route("**/verified-records", route => {
    const claim = route.request().postDataJSON().request.draft.claims[0].claim_id;
    if (malformed === "truncated") return route.fulfill({ contentType: "application/json", body: '{"records":[' });
    return route.fulfill({ json: { representation_version: malformed === "wrong version" ? "unknown" : "record-response-json-v1", records: [{ claim_id: malformed === "wrong claim" ? other : claim, text: "INVALID RELEASE" }], ...(malformed === "extra content" ? { verified: true } : {}) } });
  });
  await requestButton(page).click();
  await expect(page.getByRole("alert", { name: "Verified record error" })).toContainText("No automatic retry");
  await expect(records(page)).toHaveCount(0);
});

test("no accepted record means no verification request", async ({ page }) => {
  await setup(page, false, true);
  await expect(requestButton(page)).toBeDisabled();
  await expect(records(page)).toHaveCount(0);
});

test("refresh discards old released restatements without requesting another", async ({ page }) => {
  await setup(page);
  let calls = 0;
  await page.route("**/verified-records", route => {
    calls++;
    return route.fulfill({ json: { representation_version: "record-response-json-v1", records: [{ claim_id: route.request().postDataJSON().request.draft.claims[0].claim_id, text: "OLD RELEASE" }] } });
  });
  await requestButton(page).click(); await expect(records(page)).toHaveCount(1);
  await page.getByRole("button", { name: "Refresh server state" }).click();
  await expect(requestButton(page)).toBeVisible();
  await expect(records(page)).toHaveCount(0);
  expect(calls).toBe(1);
});
