import { test, expect, Page } from "@playwright/test";

const patient = "11111111-1111-4111-8111-111111111111";
const other = "22222222-2222-4222-8222-222222222222";
const claim = "33333333-3333-4333-8333-333333333333";
const panel = (page: Page) => page.getByRole("region", { name: "Record questions", exact: true });
const record = (page: Page) => page.getByRole("article", { name: "Released question record", exact: true });
const output = { representation_version: "record-response-json-v1", records: [{ claim_id: claim, text: "Synthetic server-only restatement" }] };

async function setup(page: Page, role = "patient") {
  await page.route("**/api/v1/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/login")) return route.fulfill({ json: { access_token: "synthetic-question-token", token_type: "bearer" } });
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { id: claim, role } });
    if (path.endsWith("/auth/profile")) return route.fulfill({ json: { profile_id: role === "patient" ? patient : claim, role } });
    if (path === "/api/v1/patients") return route.fulfill({ json: [{ patient_id: patient }, { patient_id: other }] });
    return route.fulfill({ json: [] });
  });
  await page.goto("/");
  await page.getByLabel("Account identity").fill("question@synthetic.invalid");
  await page.getByLabel("Password", { exact: true }).fill("synthetic-question-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  if (role === "practitioner") await page.getByLabel("Select authorized patient").selectOption(patient);
  await expect(panel(page)).toBeVisible();
}

async function ask(page: Page) {
  await panel(page).getByLabel("Record question", { exact: true }).fill("What confirmed clinical information is recorded?");
  await panel(page).getByRole("button", { name: "Ask records", exact: true }).click();
}

for (const role of ["patient", "practitioner"]) test(`${role} renders only released text and uses fresh operation IDs`, async ({ page }) => {
  await setup(page, role);
  const operations: string[] = [];
  await page.route("**/record-questions", route => {
    const body = route.request().postDataJSON();
    expect(Object.keys(body).sort()).toEqual(["operation_id", "question"]);
    expect(body.question).toBe("What confirmed clinical information is recorded?");
    expect(body.operation_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(route.request().headers().authorization).toBe("Bearer synthetic-question-token");
    operations.push(body.operation_id);
    return route.fulfill({ json: output });
  });
  await ask(page);
  await expect(record(page)).toContainText("Synthetic server-only restatement");
  await ask(page);
  await expect(record(page)).toHaveCount(1);
  expect(operations.length === 2 && operations[0] !== operations[1]).toBe(true);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length, document.cookie])).toEqual([0, 0, ""]);
});

for (const reason of ["QUESTION_UNSUPPORTED", "NO_MATCHING_RECORDS", "LATEST_AMBIGUOUS", "TOO_MANY_RECORDS", "CLINICAL_DECISION_UNSUPPORTED"]) test(`safe fixed status ${reason}`, async ({ page }) => {
  await setup(page);
  await page.route("**/record-questions", route => route.fulfill({ json: {
    representation_version: "record-question-status-v1", reason_code: reason,
    status: reason === "CLINICAL_DECISION_UNSUPPORTED" ? "REFUSE" : "CLARIFY",
  } }));
  await ask(page);
  await expect(panel(page).getByRole("status")).toBeVisible();
  await expect(record(page)).toHaveCount(0);
});

for (const status of [401, 403, 409, 422, 503]) test(`failure ${status} withholds body`, async ({ page }) => {
  await setup(page);
  let posts = 0;
  await page.route("**/record-questions", route => { posts++; return route.fulfill({ status, json: output }); });
  await ask(page);
  await expect(page.getByRole("alert", { name: status === 401 || status === 403 ? "Request error" : "Record question error", exact: true })).toBeVisible();
  await expect(record(page)).toHaveCount(0);
  await expect(page.getByText("Synthetic server-only restatement", { exact: true })).toHaveCount(0);
  expect(posts).toBe(1);
});

test("unknown or forged release representation is not rendered", async ({ page }) => {
  await setup(page);
  await page.route("**/record-questions", route => route.fulfill({ json: { ...output, PASS: true, draft: "PRIVATE DRAFT" } }));
  await ask(page);
  await expect(panel(page).getByRole("alert")).toBeVisible();
  await expect(record(page)).toHaveCount(0);
  await expect(page.getByText("PRIVATE DRAFT", { exact: true })).toHaveCount(0);
});

test("lost acknowledgement never retries and a new explicit request has a fresh UUID", async ({ page }) => {
  await setup(page);
  const operations: string[] = [];
  await page.route("**/record-questions", route => {
    operations.push(route.request().postDataJSON().operation_id);
    return operations.length === 1 ? route.abort("failed") : route.fulfill({ json: output });
  });
  await ask(page);
  await expect(panel(page).getByRole("alert")).toBeVisible();
  await expect(record(page)).toHaveCount(0);
  expect(operations).toHaveLength(1);
  await ask(page);
  await expect(record(page)).toHaveCount(1);
  expect(operations.length === 2 && operations[0] !== operations[1]).toBe(true);
});

test("workspace refresh clears an already released question response", async ({ page }) => {
  await setup(page);
  await page.route("**/record-questions", route => route.fulfill({ json: output }));
  await ask(page);
  await expect(record(page)).toHaveCount(1);
  await page.getByRole("button", { name: "Refresh server state", exact: true }).click();
  await expect(panel(page)).toBeVisible();
  await expect(record(page)).toHaveCount(0);
  await expect(panel(page).getByLabel("Record question", { exact: true })).toHaveValue("");
});

for (const action of ["duplicate", "switch", "sign-out", "refresh"]) test(`pending question ${action}`, async ({ page }) => {
  await setup(page, "practitioner");
  let release!: () => void, entered!: () => void, finished!: () => void;
  const gate = new Promise<void>(r => { release = r; });
  const started = new Promise<void>(r => { entered = r; });
  const completed = new Promise<void>(r => { finished = r; });
  let posts = 0;
  await page.route("**/record-questions", async route => {
    posts++; entered(); await gate;
    try { await route.fulfill({ json: output }); } catch { /* Context cancellation. */ } finally { finished(); }
  });
  try {
    await panel(page).getByLabel("Record question", { exact: true }).fill("What medications are currently recorded?");
    await panel(page).getByRole("button", { name: "Ask records", exact: true }).evaluate(b => { (b as HTMLButtonElement).click(); (b as HTMLButtonElement).click(); });
    await started;
    if (action === "switch") await page.getByLabel("Select authorized patient").selectOption(other);
    if (action === "sign-out") await page.getByRole("button", { name: "Sign out", exact: true }).click();
    if (action === "refresh") await page.getByRole("button", { name: "Refresh server state", exact: true }).click();
    release(); await completed;
    if (action === "duplicate") await expect(record(page)).toHaveCount(1);
    else { await expect(record(page)).toHaveCount(0); await expect(page.getByText("Synthetic server-only restatement", { exact: true })).toHaveCount(0); }
    expect(posts).toBe(1);
  } finally { release(); }
});
