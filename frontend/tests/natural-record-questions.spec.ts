import { expect, Page, test } from "@playwright/test";

const patient = "11111111-1111-4111-8111-111111111111";
const other = "22222222-2222-4222-8222-222222222222";
const actor = "33333333-3333-4333-8333-333333333333";
const profile = "44444444-4444-4444-8444-444444444444";
const claim = "55555555-5555-4555-8555-555555555555";
const released = "Recorded practitioner_managed laboratory fact (observed 2026-01-15T10:00:00Z): HbA1c 7.1 %.";
const previous = "Recorded practitioner_managed laboratory fact (observed 2025-10-15T10:00:00Z): HbA1c 6.9 %.";

async function setup(page: Page, mode: "normal" | "missing" | "refuse" | "malformed" | "network" = "normal", hold?: Promise<void>) {
  const calls: { patient: string; operation_id: string; question: string; previous_questions?: string[] }[] = [];
  await page.route("**/api/v1/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/login")) return route.fulfill({ json: { access_token: "synthetic-token", token_type: "bearer" } });
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { id: actor, role: "practitioner" } });
    if (path.endsWith("/auth/profile")) return route.fulfill({ json: { profile_id: profile, role: "practitioner" } });
    if (path === "/api/v1/patients") return route.fulfill({ json: [{ patient_id: patient }, { patient_id: other }] });
    if (path.endsWith("/facts")) {
      const id = path.includes(other) ? other : patient;
      return route.fulfill({ json: [{ fact_id: id, version_id: id, version: 1, previous_version_id: null,
        patient_id: id, status: "CONFIRMED", author_id: actor, author_role: "practitioner", attribution: "practitioner_managed",
        fact: { kind: "laboratory", code: "hba1c", value: "7.1", unit: "%" }, observed_at: "2026-01-15T10:00:00Z",
        entered_at: "2026-01-15T11:00:00Z", provenance: { source: "synthetic fixture", reference: null, data_origin: "synthetic" },
        entry_method: "STRUCTURED", correction_reason: null, confirmed_by_id: null, confirmed_at: null }] });
    }
    if (path.endsWith("/confirmations")) return route.fulfill({ json: [] });
    if (path.endsWith("/record-questions")) {
      const body = request.postDataJSON();
      expect(Object.keys(body).sort()).toEqual(body.previous_questions ? ["operation_id", "previous_questions", "question"] : ["operation_id", "question"]);
      calls.push({ ...body, patient: path.includes(other) ? other : patient });
      if (hold && calls.length === 1) await hold;
      if (mode === "network") return route.abort();
      if (mode === "missing" || mode === "refuse") return route.fulfill({ json: {
        representation_version: "record-question-status-v1", status: mode === "missing" ? "CLARIFY" : "REFUSE",
        reason_code: mode === "missing" ? "NO_MATCHING_RECORDS" : "CLINICAL_DECISION_UNSUPPORTED",
      } });
      return route.fulfill({ json: { representation_version: "record-response-json-v1",
        records: [{ claim_id: claim, text: mode === "malformed" ? "PRIVATE_UNVERIFIED_PROSE" : calls.length === 1 ? released : previous }],
        ...(mode === "malformed" ? { PASS: true } : {}) } }).catch(() => {});
    }
    return route.fulfill({ status: 404, json: {} });
  });
  await page.goto("/");
  await page.getByLabel("Account identity").fill("doctor-demo@synthetic.invalid");
  await page.getByLabel("Password", { exact: true }).fill("demo1234");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.locator(`button[title="${patient}"]`).click();
  await page.getByRole("button", { name: "Ask Patient Records", exact: true }).click();
  return calls;
}

async function ask(page: Page, question: string) {
  await page.getByLabel("Record question", { exact: true }).fill(question);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
}

test("natural question and follow-up retain bounded user context and fresh operation IDs", async ({ page }) => {
  const calls = await setup(page);
  await ask(page, "Can you show me my last A1c result?");
  await expect(page.getByLabel("Record assistant response").first()).toContainText(released);
  await ask(page, "And the one before that?");
  await expect(page.getByLabel("Record assistant response").last()).toContainText(previous);
  const log = page.getByRole("log", { name: "Patient conversation" });
  await expect(log.getByLabel("Your record question")).toHaveText(["Can you show me my last A1c result?", "And the one before that?"]);
  await expect(log.getByLabel("Record assistant response").first()).toContainText(released);
  const questionBounds = await log.getByLabel("Your record question").last().boundingBox();
  const answerBounds = await log.getByLabel("Record assistant response").last().boundingBox();
  expect(questionBounds!.x).toBeGreaterThan(answerBounds!.x);
  expect(answerBounds!.y).toBeGreaterThanOrEqual(questionBounds!.y + questionBounds!.height);
  await expect(page.locator(".cc-input-area").getByLabel("Record assistant response")).toHaveCount(0);
  expect(calls[0].previous_questions).toBeUndefined();
  expect(calls[1].previous_questions).toEqual(["Can you show me my last A1c result?"]);
  expect(calls[1].operation_id).not.toBe(calls[0].operation_id);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
});

test("message history scrolls while input stays docked and records sidebar remains", async ({ page }) => {
  await setup(page);
  const log = page.getByRole("log", { name: "Patient conversation" });
  const input = page.getByLabel("Record question", { exact: true });
  const initial = await input.boundingBox();
  for (let index = 0; index < 8; index++) {
    await ask(page, `Show the recorded A1c result again ${index + 1}`);
    await expect(log.getByLabel("Record assistant response")).toHaveCount(index + 1);
    await expect(log.getByLabel("Record assistant response").last()).toHaveAttribute("aria-busy", "false");
  }
  expect(await log.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
  const final = await input.boundingBox();
  expect(Math.abs(final!.y - initial!.y)).toBeLessThan(2);
  const dock = await page.locator(".cc-input-area").boundingBox();
  expect(Math.abs(dock!.y + dock!.height - page.viewportSize()!.height)).toBeLessThan(2);
  await expect(page.locator(".cc-records-panel")).toBeVisible();
  await expect(log.getByLabel("Your record question")).toHaveCount(8);
});

for (const mode of ["missing", "refuse", "malformed", "network"] as const) test(`${mode} never displays fabricated prose or automatically retries`, async ({ page }) => {
  const calls = await setup(page, mode);
  await ask(page, mode === "refuse" ? "Should I increase metformin?" : "Is there a recorded insulin dose?");
  await expect(page.locator("body")).toContainText(mode === "missing" ? "No matching accepted record" : mode === "refuse" ? "treatment decisions are not supported" : "No verified answer is available");
  await expect(page.getByRole("log", { name: "Patient conversation" })).toContainText(mode === "missing" ? "No matching accepted record" : mode === "refuse" ? "treatment decisions are not supported" : "No verified answer is available");
  await expect(page.locator(".cc-input-area").getByRole("alert")).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText("PRIVATE_UNVERIFIED_PROSE");
  expect(calls).toHaveLength(1);
});

for (const action of ["switch", "sign-out"]) test(`late answer withheld after ${action}`, async ({ page }) => {
  let release!: () => void;
  const hold = new Promise<void>(resolve => { release = resolve; });
  const calls = await setup(page, "normal", hold);
  await ask(page, "Can you show me my last A1c result?");
  await expect.poll(() => calls.length).toBe(1);
  if (action === "switch") await page.locator(`button[title="${other}"]`).click();
  else await page.getByRole("button", { name: "Sign out", exact: true }).click();
  release();
  await expect(page.getByLabel("Record assistant response").filter({ hasText: released })).toHaveCount(0);
  if (action === "switch") {
    await ask(page, "Which medicines are on this chart?");
    await expect(page.getByLabel("Record assistant response").last()).toContainText(previous);
    expect(calls[1].patient).toBe(other);
    expect(calls[1].previous_questions).toBeUndefined();
  } else await expect(page.getByLabel("Account identity")).toBeVisible();
});
