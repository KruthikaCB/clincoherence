import { selectPatient } from "./dashboard";
import { test, expect, Page } from "@playwright/test";
import bundle from "../../backend/app/fixtures/evidence/bundle.json";
import rules from "../../backend/app/fixtures/evidence/rules.json";
import approvals from "../../backend/app/fixtures/evidence/approvals.json";

const patient = "11111111-1111-4111-8111-111111111111";
const other = "22222222-2222-4222-8222-222222222222";
const claim = "33333333-3333-4333-8333-333333333333";
const question = "What is my latest recorded hba1c?";
const panel = (page: Page) => page.locator(".cc-input-area");
const provenance = (page: Page) => page.getByRole("region", { name: "Released synthetic provenance", exact: true });
const records = (page: Page) => page.locator(".cc-msg-assistant.result");
function responseBody() {
  const rule = rules.rules[0];
  return { representation_version: "record-evidence-json-v2", records: [{ claim_id: claim, text: "Synthetic server-rendered HbA1c record" }], provenance: [{
    claim_id: claim, fact_version_ids: [claim], label: "SYNTHETIC_FIXTURE_ONLY", source: bundle.sources[0],
    evidence: rule.evidence[0], locator: bundle.units[0].locator,
    rule_id: rule.rule_id, rule_version: rule.version, rule_content_hash: rule.content_hash,
    approval_manifest_id: approvals.manifest_id, approval_manifest_version: approvals.version,
    approval_manifest_hash: "a".repeat(64), intended_use: "synthetic-context-testing",
    integrity_status: "RESOLVED", applicability_status: "RESOLVED", clinical_support_asserted: false,
  }] };
}

async function setup(page: Page, role = "patient") {
  await page.route("**/api/v1/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/login")) return route.fulfill({ json: { access_token: "synthetic-evidence-token", token_type: "bearer" } });
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { id: claim, role } });
    if (path.endsWith("/auth/profile")) return route.fulfill({ json: { profile_id: role === "patient" ? patient : claim, role } });
    if (path === "/api/v1/patients") return route.fulfill({ json: [{ patient_id: patient }, { patient_id: other }] });
    return route.fulfill({ json: [] });
  });
  await page.goto("/");
  await page.getByLabel("Account identity").fill("evidence@synthetic.invalid");
  await page.getByLabel("Password", { exact: true }).fill("synthetic-evidence-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  if (role === "practitioner") await selectPatient(page, patient);
  await page.getByRole("button", { name: "Ask Patient Records", exact: true }).click();
  await expect(panel(page)).toBeVisible();
  await panel(page).getByLabel("Record question", { exact: true }).fill(question);
  await panel(page).getByRole("checkbox", { name: "Include synthetic demonstration provenance", exact: true }).check();
}

for (const role of ["patient", "practitioner"]) test(`${role} displays released synthetic metadata with explicit limits`, async ({ page }) => {
  await setup(page, role);
  await page.route("**/record-questions", route => {
    const body = route.request().postDataJSON();
    expect(Object.keys(body).sort()).toEqual(["include_synthetic_provenance", "operation_id", "question"]);
    expect(body.include_synthetic_provenance === true && body.question === question).toBe(true);
    return route.fulfill({ json: responseBody() });
  });
  await panel(page).getByRole("button", { name: "Ask", exact: true }).click();
  await expect(records(page)).toHaveCount(1);
  await expect(provenance(page)).toContainText("SYNTHETIC DEMONSTRATION DATA — NOT CLINICAL GUIDANCE");
  await expect(provenance(page)).toContainText(bundle.sources[0].title);
  await expect(provenance(page)).toContainText(bundle.units[0].content_hash);
  await expect(provenance(page)).toContainText("Neither establishes clinical correctness or clinical support");
  await expect(provenance(page).getByRole("link")).toHaveCount(0);
  await panel(page).getByRole("checkbox").uncheck();
  // Changing the next request option does not rewrite prior chat messages.
  await expect(records(page)).toHaveCount(1);
  await expect(provenance(page)).toHaveCount(1);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length, document.cookie])).toEqual([0, 0, ""]);
});

for (const fault of ["claim", "status", "hash", "source", "support", "extra", "v1"]) test(`withholds all content for invalid provenance: ${fault}`, async ({ page }) => {
  await setup(page);
  const body = structuredClone(responseBody());
  const p = body.provenance[0];
  if (fault === "claim") p.claim_id = other;
  if (fault === "status") p.applicability_status = "NOT_APPLICABLE";
  if (fault === "hash") p.evidence.source_content_hash = "b".repeat(64);
  if (fault === "source") p.source.canonical_location = "https://untrusted.invalid/source";
  if (fault === "support") p.clinical_support_asserted = true;
  await page.route("**/record-questions", route => route.fulfill({ json: fault === "v1" ? { representation_version: "record-response-json-v1", records: body.records } : fault === "extra" ? { ...body, approved: true } : body }));
  await panel(page).getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.locator(".cc-msg-assistant.error")).toBeVisible();
  await expect(records(page)).toHaveCount(0);
  await expect(provenance(page)).toHaveCount(0);
});

for (const action of ["switch", "sign-out", "refresh", "revoke"]) test(`provenance is withheld after ${action}`, async ({ page }) => {
  await setup(page, "practitioner");
  let release!: () => void, entered!: () => void, finished!: () => void;
  const gate = new Promise<void>(r => { release = r; });
  const started = new Promise<void>(r => { entered = r; });
  const completed = new Promise<void>(r => { finished = r; });
  await page.route("**/record-questions", async route => {
    entered(); await gate;
    try { await route.fulfill({ status: action === "revoke" ? 403 : 200, json: responseBody() }); } catch { /* Unmounted request. */ } finally { finished(); }
  });
  try {
    await panel(page).getByRole("button", { name: "Ask", exact: true }).click(); await started;
    if (action === "switch") await selectPatient(page, other);
    if (action === "sign-out") await page.getByRole("button", { name: "Sign out", exact: true }).click();
    if (action === "refresh") await page.getByRole("button", { name: "Refresh server state", exact: true }).click();
    release(); await completed;
    if (action === "revoke") await expect(page.getByRole("alert", { name: "Request error", exact: true })).toBeVisible();
    await expect(records(page)).toHaveCount(0); await expect(provenance(page)).toHaveCount(0);
  } finally { release(); }
});
