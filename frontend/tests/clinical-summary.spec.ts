import { expect, Page, test } from "@playwright/test";

const patient = "11111111-1111-4111-8111-111111111111";
const other = "22222222-2222-4222-8222-222222222222";
const actor = "33333333-3333-4333-8333-333333333333";
const profile = "44444444-4444-4444-8444-444444444444";
const id = (n: number) => `90000000-0000-4000-8000-${String(n).padStart(12, "0")}`;

// Full released contract, including independently verified source records.
function source(n: number, fact: object, observed_at: string, text: string) {
  return { source: { version_id: id(n), fact_id: id(n), patient_id: patient, version: 1,
    previous_version_id: null, previous_version: null, attribution: "practitioner_managed",
    author_id: actor, author_role: "practitioner", fact, observed_at,
    entered_at: "2026-03-01T10:00:00Z", correction_reason: null,
    provenance: { source: "Synthetic summary fixture", reference: "synthetic-reference", data_origin: "synthetic" },
    entry_method: "STRUCTURED", status: "CONFIRMED", confirmed_by_id: null, confirmed_at: null },
  verification: { claim_id: id(n + 100), outcome: "GROUNDED", reason_code: "FACT_RESTATEMENT_MATCH",
    fact_version_ids: [id(n)], rendered_text: text, text_verified: true, checks_run: [], checks_not_applicable: [] } };
}
function response() {
  const lab = (n: number, code: string, value: string, unit: string, time: string) => ({
    observed_at: time, status: "RECORDED", value, unit,
    records: [source(n, { kind: "laboratory", code, value, unit }, time, `${code} ${value} ${unit} recorded on ${time}.`)],
  });
  const latest = lab(1, "hba1c", "7.1000000000", "%", "2026-02-01T09:00:00Z");
  const previous = lab(2, "hba1c", "7.8", "%", "2026-01-01T09:00:00Z");
  return { representation_version: "clinical-summary-json-v1", verification_scope: "RECORDED_STATE_ONLY_NOT_CLINICAL_CORRECTNESS",
    summary: { patient_id: patient, policy_version: "recorded-t2dm-summary-v1", verification_policy_version: "structured-fact-restatement-v1",
      renderer_version: "record-restatement-text-v2", laboratory_policy_version: "laboratory-explicit-pairs-v1",
      snapshot: { current_version_ids: [id(1), id(2), id(3), id(4)], history_version_ids: [id(1), id(2), id(3), id(4)] },
      hba1c_observations: [latest, previous], latest_hba1c: latest, previous_hba1c: previous,
      hba1c_change: { direction: "DECREASED", reason_code: "RECORDED_VALUES_COMPARED", comparison: {
        rule_id: "LC-LAB-DIFFERENCE-01", earlier_version_id: id(2), later_version_id: id(1), numeric_difference: "-0.7", unit: "%" } },
      latest_egfr: lab(3, "egfr", "58", "mL/min/1.73m2", "2026-02-02T09:00:00Z"),
      medications: [source(4, { kind: "medication", medication_name: "metformin", dose_value: "500", dose_unit: "mg", frequency: "twice daily" },
        "2026-02-01T09:00:00Z", "Metformin 500 mg twice daily recorded on 2026-02-01.")],
      missing_information: [] as string[], text: "SERVER_SUMMARY_TEXT_NOT_USED_AS_A_DRAFT" } };
}
type Options = { role?: "patient" | "practitioner"; status?: number; body?: unknown; hold?: Promise<void>; network?: boolean };
async function setup(page: Page, options: Options = {}) {
  const calls: { operation_id: string; request: { patient_id: string; representation_version: string } }[] = [];
  const role = options.role ?? "practitioner";
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/login")) return route.fulfill({ json: { access_token: "synthetic-token", token_type: "bearer" } });
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { id: actor, role } });
    if (path.endsWith("/auth/profile")) return route.fulfill({ json: { profile_id: role === "patient" ? patient : profile, role } });
    if (path === "/api/v1/patients") return route.fulfill({ json: [{ patient_id: patient }, { patient_id: other }] });
    if (path.endsWith("/facts") || path.endsWith("/confirmations") || path.endsWith("/consents")) return route.fulfill({ json: [] });
    if (path.endsWith("/clinical-summary")) {
      expect(route.request().method()).toBe("POST");
      expect(route.request().headers().authorization).toBe("Bearer synthetic-token");
      calls.push(route.request().postDataJSON());
      if (options.hold) await options.hold;
      if (options.network) return route.abort();
      return route.fulfill({ status: options.status ?? 200, json: options.body ?? response() }).catch(() => {});
    }
    return route.fulfill({ status: 404, json: {} });
  });
  await page.goto("/");
  await page.getByLabel("Account identity").fill("doctor-demo@synthetic.invalid");
  await page.getByLabel("Password", { exact: true }).fill("demo1234");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  if (role === "practitioner") await page.locator(`button[title="${patient}"]`).click();
  await page.getByRole("button", { name: "Clinical Summary", exact: true }).click();
  return calls;
}
const load = (page: Page) => page.getByRole("button", { name: "Request clinical summary", exact: true }).click();

for (const role of ["patient", "practitioner"] as const) test(`${role}: released summary, provenance and fresh operation IDs`, async ({ page }) => {
  const calls = await setup(page, { role });
  await load(page);
  const panel = page.getByRole("region", { name: "Clinical Summary", exact: true });
  await expect(panel.getByLabel("Latest HbA1c", { exact: true })).toContainText("7.1000000000 %");
  await expect(panel.getByLabel("Previous HbA1c", { exact: true })).toContainText("7.8 %");
  await expect(panel.getByLabel("Latest eGFR", { exact: true })).toContainText("58 mL/min/1.73m2");
  await expect(panel).toContainText("Metformin 500 mg twice daily recorded");
  await expect(panel).toContainText("do not confirm current use or adherence");
  await expect(panel).toContainText("Decreased");
  await panel.getByLabel("Latest HbA1c", { exact: true }).getByText("Source provenance", { exact: true }).click();
  await expect(panel.getByLabel("Latest HbA1c", { exact: true })).toContainText("Synthetic summary fixture");
  expect(calls[0]).toEqual({ operation_id: expect.stringMatching(/^[0-9a-f-]{36}$/), request: { patient_id: patient, representation_version: "clinical-summary-json-v1" } });
  await load(page);
  await expect.poll(() => calls.length).toBe(2);
  expect(calls[1].operation_id).not.toBe(calls[0].operation_id);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
});

test("missing data and conflicting observations never select a fabricated value", async ({ page }) => {
  const body = response(), s = body.summary;
  const latest = { ...s.latest_hba1c, status: "AMBIGUOUS", value: null, unit: null,
    records: [...s.latest_hba1c.records, source(5, { kind: "laboratory", code: "hba1c", value: "8", unit: "%" }, s.latest_hba1c.observed_at, "Conflicting recorded HbA1c 8 %.")] };
  const fixture = { ...body, summary: { ...s, snapshot: { current_version_ids: [id(1), id(5)], history_version_ids: [id(1), id(5)] },
    hba1c_observations: [latest], latest_hba1c: latest, previous_hba1c: null, latest_egfr: null, medications: [],
    missing_information: ["previous_hba1c", "egfr", "medications"], hba1c_change: { direction: "NOT_COMPARABLE", reason_code: "AMBIGUOUS_OBSERVATION", comparison: null } } };
  await setup(page, { body: fixture }); await load(page);
  await expect(page.getByLabel("Latest HbA1c", { exact: true })).toContainText("Conflicting values");
  await expect(page.getByLabel("Latest eGFR", { exact: true })).toContainText("Not recorded");
  await expect(page.getByRole("region", { name: "Clinical Summary" })).toContainText("Previous HbA1c, eGFR, Recorded medications");
});

for (const status of [401, 403, 409, 503]) test(`${status} response cannot disclose summary body`, async ({ page }) => {
  const calls = await setup(page, { status }); await load(page);
  await expect(page.getByRole("alert").first()).toBeVisible();
  await expect(page.locator("body")).not.toContainText("7.1000000000");
  await expect(page.locator("body")).not.toContainText("Metformin 500");
  expect(calls).toHaveLength(1);
});

for (const fault of ["patient", "source", "verification", "representation"]) test(`malformed ${fault} is withheld`, async ({ page }) => {
  const body = response();
  if (fault === "patient") body.summary.patient_id = other;
  if (fault === "source") body.summary.latest_hba1c.records[0].source.patient_id = other;
  if (fault === "verification") body.summary.medications[0].verification.text_verified = false;
  if (fault === "representation") body.representation_version = "unknown";
  await setup(page, { body }); await load(page);
  await expect(page.getByRole("alert", { name: "Summary error" })).toBeVisible();
  await expect(page.locator("body")).not.toContainText("7.1000000000");
  await expect(page.locator("body")).not.toContainText("Metformin 500");
});

for (const action of ["switch", "sign-out"] as const) test(`pending summary discarded after ${action}`, async ({ page }) => {
  let release!: () => void;
  const hold = new Promise<void>(resolve => { release = resolve; });
  const calls = await setup(page, { hold }); await load(page);
  await expect.poll(() => calls.length).toBe(1);
  await expect(page.getByRole("button", { name: "Loading summary…" })).toBeDisabled();
  if (action === "switch") await page.locator(`button[title="${other}"]`).click();
  else await page.getByRole("button", { name: "Sign out", exact: true }).click();
  release();
  if (action === "sign-out") await expect(page.getByLabel("Account identity")).toBeVisible();
  else {
    // Patient selection intentionally returns the dashboard to its chat tab.
    await page.getByRole("button", { name: "Clinical Summary", exact: true }).click();
    await expect(page.getByRole("button", { name: "Request clinical summary" })).toBeVisible();
  }
  await expect(page.locator("body")).not.toContainText("7.1000000000");
  expect(calls).toHaveLength(1);
});

test("uncertain network outcome is not automatically retried", async ({ page }) => {
  const calls = await setup(page, { network: true }); await load(page);
  await expect(page.getByRole("alert", { name: "Summary error" })).toContainText("No automatic retry");
  expect(calls).toHaveLength(1);
});
