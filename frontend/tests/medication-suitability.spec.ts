import { expect, Page, test } from "@playwright/test";
import catalog from "../../backend/app/fixtures/medication_suitability/catalog.v1.json";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const patient = "11111111-1111-4111-8111-111111111111";
const other = "22222222-2222-4222-8222-222222222222";
const actor = "33333333-3333-4333-8333-333333333333";
const id = "90000000-0000-4000-8000-000000000001";
function response() {
  return {
    representation_version: "synthetic-medication-assessment-v1", patient_id: patient,
    mode: "SYNTHETIC_EDUCATIONAL_ONLY", confirmation_scope: "SPEAKER_CONFIRMED_NOT_CLINICIAN_APPROVED",
    assessed_at: "2026-11-12T12:00:00Z", catalog_version: catalog.catalog_version,
    catalog_sha256: createHash("sha256").update(readFileSync(resolve("../backend/app/fixtures/medication_suitability/catalog.v1.json"))).digest("hex"),
    evidence_review_status: catalog.review_status, limitations: ["Synthetic only; no prescription or dose."],
    facts: [{ version_id: id, fact_id: id, patient_id: patient, version: 1, previous_version_id: null, previous_version: null,
      attribution: "practitioner_managed", author_id: actor, author_role: "practitioner",
      fact: { kind: "laboratory", code: "egfr", value: "42", unit: "mL/min/1.73m2" },
      observed_at: "2026-02-01T09:00:00Z", entered_at: "2026-03-01T10:00:00Z", correction_reason: null,
      provenance: { source: "Synthetic scenario A", reference: null, data_origin: "synthetic" },
      entry_method: "STRUCTURED", status: "CONFIRMED", confirmed_by_id: null, confirmed_at: null }],
    assessments: ["metformin", "empagliflozin", "semaglutide"].map(medication => ({
      medication, status: medication === "metformin" ? "CAUTION_REVIEW_REQUIRED" : "INSUFFICIENT_INFORMATION",
      reasoning: ["Synthetic recorded context; no positive clearance."], cautions: ["Clinician review required."],
      missing_information: ["Separate clinician approval", "Missing safety information: UNKNOWN"], fact_version_ids: [id],
      clinician_review_required: true,
      rule_checks: catalog.rules.filter(r => r.medication === medication).map(rule => ({ rule, outcome: "UNKNOWN", fact_version_ids: [id] })),
      evidence: catalog.sources.filter(s => catalog.rules.some(r => r.medication === medication && r.source_id === s.source_id)),
    })),
  };
}
type Options = { body?: unknown; status?: number; hold?: Promise<void>; role?: string };
async function setup(page: Page, options: Options = {}) {
  const calls: string[] = [];
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/login")) return route.fulfill({ json: { access_token: "synthetic-token", token_type: "bearer" } });
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { id: actor, role: options.role ?? "practitioner" } });
    if (path.endsWith("/auth/profile")) return route.fulfill({ json: { profile_id: patient, role: options.role ?? "practitioner" } });
    if (path === "/api/v1/patients") return route.fulfill({ json: [{ patient_id: patient }, { patient_id: other }] });
    if (path.endsWith("/facts") || path.endsWith("/confirmations") || path.endsWith("/consents")) return route.fulfill({ json: [] });
    if (path.endsWith("/medication-suitability")) {
      expect(route.request().method()).toBe("GET");
      expect(route.request().headers().authorization).toBe("Bearer synthetic-token");
      calls.push(path);
      if (options.hold) await options.hold;
      return route.fulfill({ status: options.status ?? 200, json: options.body ?? response() }).catch(() => {});
    }
    return route.fulfill({ status: 404, json: {} });
  });
  await page.goto("/");
  await page.getByLabel("Account identity").fill("capstone@synthetic.invalid");
  await page.getByLabel("Password", { exact: true }).fill("synthetic-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  if (options.role !== "patient") {
    await page.locator(`button[title="${patient}"]`).click();
    await page.getByRole("button", { name: "Medication Suitability", exact: true }).click();
  }
  return calls;
}
const load = (page: Page) => page.getByRole("button", { name: "Assess synthetic scenario", exact: true }).click();

test("three educational cards, provenance, evidence and no browser persistence", async ({ page }, testInfo) => {
  await setup(page); await load(page);
  await expect(page.getByLabel("metformin assessment")).toContainText("CAUTION / REVIEW REQUIRED");
  await expect(page.getByLabel("empagliflozin assessment")).toContainText("INSUFFICIENT INFORMATION");
  await expect(page.getByLabel("semaglutide assessment")).toContainText("UNKNOWN");
  await expect(page.getByText("Synthetic educational scenario — not a live clinical recommendation.")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("mocked-synthetic-overview.png"), fullPage: true });
  const card = page.getByLabel("metformin assessment");
  await card.getByText("Patient facts and exact versions", { exact: true }).click();
  await expect(card).toContainText("42 mL/min/1.73m2");
  await expect(card).toContainText(id);
  await card.getByText("Rules and evidence references", { exact: true }).click();
  await expect(card.getByRole("link")).toHaveAttribute("href", /https:\/\/dailymed\.nlm\.nih\.gov/);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
  await page.screenshot({ path: testInfo.outputPath("mocked-synthetic-panel.png"), fullPage: true });
});

test("switching clears A and renders a distinct authorized B response", async ({ page }) => {
  await setup(page); await load(page);
  await expect(page.getByLabel("metformin assessment")).toContainText("CAUTION / REVIEW REQUIRED");
  const body = response();
  body.patient_id = other; body.facts[0].patient_id = other; body.facts[0].fact.value = "25";
  body.assessments[0].status = "NOT_SUITABLE_UNDER_DOCUMENTED_RULE";
  await page.route(`**/patients/${other}/medication-suitability`, route => route.fulfill({ json: body }));
  await page.locator(`button[title="${other}"]`).click();
  await page.getByRole("button", { name: "Medication Suitability", exact: true }).click();
  await expect(page.getByLabel("metformin assessment")).toHaveCount(0);
  await load(page);
  await expect(page.getByRole("region", { name: "Medication Suitability" })).toContainText(other);
  await expect(page.getByLabel("metformin assessment")).toContainText("NOT SUITABLE UNDER DOCUMENTED RULE");
});

test("evidence unavailable withholds all verdicts", async ({ page }) => {
  const body = response();
  const unavailable = { ...body, evidence_review_status: "UNAVAILABLE", catalog_version: null, catalog_sha256: null,
    assessments: body.assessments.map(c => ({ ...c, status: "INSUFFICIENT_INFORMATION", rule_checks: [], evidence: [] })) };
  await setup(page, { body: unavailable }); await load(page);
  await expect(page.getByText("Unavailable — all rules withheld", { exact: false })).toBeVisible();
  await expect(page.getByLabel("metformin assessment")).toContainText("INSUFFICIENT INFORMATION");
});

for (const fault of ["patient", "fact", "positive", "origin", "reference"] as const) test(`reject malformed ${fault}`, async ({ page }) => {
  const body = response();
  if (fault === "patient") body.patient_id = other;
  if (fault === "fact") body.facts[0].patient_id = other;
  if (fault === "positive") body.assessments[0].status = "POTENTIALLY_SUITABLE";
  if (fault === "origin") body.facts[0].provenance.data_origin = "de_identified";
  if (fault === "reference") body.assessments[0].fact_version_ids = [other];
  await setup(page, { body }); await load(page);
  await expect(page.getByRole("region", { name: "Medication Suitability" }).getByRole("alert")).toContainText("Assessment unavailable");
  await expect(page.getByLabel("metformin assessment")).toHaveCount(0);
});

for (const status of [401, 403, 409, 503]) test(`HTTP ${status} cannot disclose response`, async ({ page }) => {
  await setup(page, { status }); await load(page);
  await expect(page.getByRole("alert").first()).toBeVisible();
  await expect(page.getByLabel("metformin assessment")).toHaveCount(0);
});

for (const action of ["switch", "sign-out"] as const) test(`late response discarded after ${action}`, async ({ page }) => {
  let release!: () => void;
  const hold = new Promise<void>(resolve => { release = resolve; });
  const calls = await setup(page, { hold }); await load(page);
  await expect.poll(() => calls.length).toBe(1);
  await expect(page.getByRole("button", { name: "Assessing records…" })).toBeDisabled();
  if (action === "switch") await page.locator(`button[title="${other}"]`).click();
  else await page.getByRole("button", { name: "Sign out", exact: true }).click();
  release();
  if (action === "switch") {
    await page.getByRole("button", { name: "Medication Suitability", exact: true }).click();
    await expect(page.getByRole("region", { name: "Medication Suitability" })).toContainText(other);
  } else await expect(page.getByLabel("Account identity")).toBeVisible();
  await expect(page.getByLabel("metformin assessment")).toHaveCount(0);
  expect(calls).toHaveLength(1);
});

test("patient role has no suitability panel", async ({ page }) => {
  await setup(page, { role: "patient" });
  await expect(page.getByRole("button", { name: "Medication Suitability", exact: true })).toHaveCount(0);
});
