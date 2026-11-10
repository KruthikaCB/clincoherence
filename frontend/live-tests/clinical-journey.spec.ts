import { test, expect, Page, BrowserContext } from "@playwright/test";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { acceptedFact, acceptedHistory, type AcceptedFact, type CorrectionRequest } from "../src/components/clinical-types";

// Live network only. No fabricated API responses, saved auth state or diagnostic attachments.
const origin = "http://127.0.0.1:3010";
function setting(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error("Guarded fixture environment is missing");
  return value;
}
const accepted = (page: Page) => page.getByRole("article", { name: "Accepted fact", exact: true });
const cards = (page: Page) => page.getByRole("article", { name: "Fact confirmation", exact: true });
const history = (page: Page) => page.getByRole("region", { name: "Accepted correction history", exact: true });
const editor = (page: Page) => page.getByRole("region", { name: "Structured correction", exact: true });
const factCard = (page: Page, fact: AcceptedFact) => accepted(page).filter({
  has: page.getByText(fact.attribution === "patient_reported" ? "Patient-reported" : "Practitioner-managed", { exact: true }),
});
const correctionPath = (fact: AcceptedFact) => `/patients/${fact.patient_id}/facts/${fact.fact_id}/corrections`;
const questions = (page: Page) => page.getByRole("region", { name: "Record questions", exact: true });
const questionRecords = (page: Page) => page.getByRole("article", { name: "Released question record", exact: true });
const questionOperations = new Set<string>();

async function askRecords(page: Page, patientId: string, question: string) {
  await questions(page).getByLabel("Record question", { exact: true }).fill(question);
  const pending = page.waitForResponse(r => r.url().endsWith(`/patients/${patientId}/record-questions`) && r.request().method() === "POST");
  await questions(page).getByRole("button", { name: "Ask records", exact: true }).click();
  const response = await pending;
  const input = response.request().postDataJSON() as { question: string; operation_id: string };
  expect(Object.keys(input).sort()).toEqual(["operation_id", "question"]);
  expect(input.question === question && /^[0-9a-f-]{36}$/.test(input.operation_id) && !questionOperations.has(input.operation_id)).toBe(true);
  questionOperations.add(input.operation_id);
  if (response.status() === 200) expect(response.headers()["cache-control"]).toBe("no-store");
  return { response, input };
}

async function questionStatus(page: Page, patientId: string, question: string, reason: string) {
  const { response } = await askRecords(page, patientId, question);
  expect(response.status()).toBe(200);
  const body = await response.json();
  expect(Object.keys(body).sort()).toEqual(["reason_code", "representation_version", "status"]);
  expect(body.representation_version === "record-question-status-v1" && body.reason_code === reason &&
    body.status === (reason === "CLINICAL_DECISION_UNSUPPORTED" ? "REFUSE" : "CLARIFY")).toBe(true);
  await expect(questions(page).getByRole("status")).toBeVisible();
  await expect(questionRecords(page)).toHaveCount(0);
}

async function questionRelease(page: Page, patientId: string, question: string, facts: AcceptedFact[], audit = false) {
  const { response, input } = await askRecords(page, patientId, question);
  expect(response.status()).toBe(200);
  const bytes = await response.body();
  const body = JSON.parse(bytes.toString()) as { representation_version: string; records: { claim_id: string; text: string }[] };
  expect(Object.keys(body).sort()).toEqual(["records", "representation_version"]);
  expect(body.representation_version === "record-response-json-v1" && body.records.length === facts.length).toBe(true);
  expect(new Set(body.records.map(r => r.claim_id)).size === facts.length).toBe(true);
  for (const row of body.records) {
    expect(Object.keys(row).sort()).toEqual(["claim_id", "text"]);
    expect(typeof row.text === "string" && facts.some(f => row.text.includes(`version ${f.version_id};`) &&
      row.text.startsWith(`Recorded ${f.attribution} ${f.fact.kind} fact (author ${f.author_role} ${f.author_id};`))).toBe(true);
  }
  // Every expected version must be represented, not just repeated text for one record.
  expect(facts.every(f => body.records.some(r => r.text.includes(`version ${f.version_id};`)))).toBe(true);
  await expect(questionRecords(page)).toHaveCount(body.records.length);
  for (const row of body.records) {
    const rendered = questionRecords(page).filter({ hasText: row.text });
    expect(await rendered.locator("p").textContent() === row.text).toBe(true);
  }
  if (audit) {
    const digest = createHash("sha256").update(bytes).digest("hex");
    const observer = spawnSync(setting("CC_LIVE_PYTHON"), [
      "-B", "-m", "tests.live_support", "observe", "--operation", input.operation_id, "--digest", digest,
    ], { cwd: setting("CC_LIVE_BACKEND"), env: process.env, encoding: "utf8", timeout: 15_000, windowsHide: true });
    expect(observer.status === 0).toBe(true);
    const metadata = JSON.parse(observer.stdout);
    expect(metadata.events === 1 && metadata.intents === 1 && metadata.pass && metadata.digest_matches).toBe(true);
  }
  return { input, body };
}

async function delayedQuestionContext(page: Page, patientId: string, changeContext: () => Promise<void>) {
  const path = `**/patients/${patientId}/record-questions`;
  let entered!: () => void, release!: () => void, finished!: () => void;
  const started = new Promise<void>(r => { entered = r; });
  const gate = new Promise<void>(r => { release = r; });
  const completed = new Promise<void>(r => { finished = r; });
  let available = false, seen = false;
  await page.route(path, async route => {
    seen = true;
    let actual: Awaited<ReturnType<typeof route.fetch>> | undefined;
    try {
      actual = await route.fetch({ maxRetries: 0 });
      available = actual.status() === 200;
      entered(); await gate;
      await route.fulfill({ response: actual }); // Real authorized bytes only; no fabricated response.
    } catch { entered(); } // Context cancellation is expected; no raw diagnostics.
    finally { try { await actual?.dispose(); } catch { available = false; } finally { finished(); } }
  });
  try {
    await questions(page).getByLabel("Record question", { exact: true }).fill("What confirmed clinical information is recorded?");
    await questions(page).getByRole("button", { name: "Ask records", exact: true }).click();
    await started; expect(available).toBe(true);
    await changeContext(); release(); await completed; expect(available).toBe(true);
    await expect(questionRecords(page)).toHaveCount(0);
  } finally { release(); if (seen) await completed; await page.unroute(path); }
}

async function openHistory(page: Page, fact: AcceptedFact, correct = false) {
  const pending = page.waitForResponse(r => r.url().endsWith(`/facts/${fact.fact_id}/history`));
  await factCard(page, fact).getByRole("button", { name: correct ? "Correct record" : "View details/history", exact: true }).click();
  const response = await pending;
  expect(response.status()).toBe(200);
  const versions = acceptedHistory(await response.json(), fact.patient_id, fact.fact_id);
  await expect(history(page)).toBeVisible();
  expect(versions.at(-1)?.version_id === fact.version_id).toBe(true);
  await expect(history(page)).toContainText(fact.provenance.source);
  await expect(history(page)).toContainText(fact.author_id);
  for (const version of versions) {
    await expect(history(page).getByRole("heading", {
      name: `Version ${version.version} — ${version.version_id === fact.version_id ? "Current accepted version" : "Superseded"}`, exact: true,
    })).toBeVisible();
  }
  return versions;
}

async function prepareCorrection(page: Page, fact: AcceptedFact, value: string, reason: string): Promise<CorrectionRequest> {
  await openHistory(page, fact, true);
  await expect(editor(page)).toBeVisible();
  const payload: CorrectionRequest = {
    fact: { kind: "laboratory", code: "hba1c", value, unit: "%" },
    observed_at: "2026-01-14T09:30:00+00:00", // Retrospective observation; version order is independent.
    provenance: { source: "Synthetic live correction", reference: "synthetic:correction-reference", data_origin: "synthetic" },
    expected_version_id: fact.version_id, correction_reason: reason,
  };
  await editor(page).getByLabel("Laboratory value", { exact: true }).fill(value);
  await editor(page).getByLabel("Observed at (ISO timestamp with timezone)").fill(payload.observed_at);
  await editor(page).getByLabel("Provenance source", { exact: true }).fill(payload.provenance.source);
  await editor(page).getByLabel("Provenance reference (optional)").fill(payload.provenance.reference!);
  await editor(page).getByLabel("Correction reason", { exact: true }).fill(reason);
  expect(await editor(page).getByLabel("Expected current version ID").inputValue() === fact.version_id).toBe(true);
  await editor(page).getByRole("button", { name: "Review correction", exact: true }).click();
  await expect(page.getByRole("region", { name: "Review new version", exact: true })).toBeVisible();
  return payload;
}

async function submitCorrection(page: Page, fact: AcceptedFact, payload: CorrectionRequest) {
  const pending = page.waitForResponse(r => r.url().endsWith(correctionPath(fact)) && r.request().method() === "POST");
  await page.getByRole("button", { name: "Submit new version", exact: true }).click();
  const response = await pending;
  expect(response.status()).toBe(201);
  const submitted = response.request().postDataJSON() as CorrectionRequest;
  expect(Object.keys(submitted).sort()).toEqual(["correction_reason", "expected_version_id", "fact", "observed_at", "provenance"]);
  expect(submitted.expected_version_id === fact.version_id && submitted.fact.kind === "laboratory" &&
    submitted.fact.value === (payload.fact.kind === "laboratory" ? payload.fact.value : null)).toBe(true);
  const next = acceptedFact(await response.json(), fact.patient_id);
  expect(next.fact_id === fact.fact_id && next.previous_version_id === fact.version_id && next.version > fact.version &&
    next.correction_reason === payload.correction_reason && next.fact.kind === "laboratory" &&
    next.fact.value === (payload.fact.kind === "laboratory" ? payload.fact.value : null)).toBe(true);
  expect(next.fact.kind === "laboratory" && next.fact.code === "hba1c" && next.fact.unit === "%" &&
    next.entry_method === "STRUCTURED" && next.attribution === fact.attribution &&
    next.provenance.source === payload.provenance.source && next.provenance.reference === payload.provenance.reference &&
    next.provenance.data_origin === "synthetic" && Date.parse(next.observed_at) === Date.parse(payload.observed_at)).toBe(true);
  await expect(page.getByRole("status").filter({ hasText: "Correction acknowledged" })).toBeVisible();
  await expect(history(page).getByRole("heading", { name: `Version ${next.version} — Current accepted version`, exact: true })).toBeVisible();
  await expect(history(page)).toContainText(fact.version_id); // Predecessor linkage is displayed.
  await expect(history(page)).toContainText(payload.correction_reason);
  await expect(history(page)).toContainText(payload.provenance.source);
  await expect(factCard(page, next)).toContainText(`Current accepted version ${next.version}`);
  await expect(factCard(page, next)).toContainText(payload.fact.kind === "laboratory" ? payload.fact.value : "");
  return next;
}

async function login(page: Page, identity: string) {
  let bearer = "";
  const capture = (request: import("@playwright/test").Request) => {
    if (request.url().startsWith(`${origin}/api/v1/`)) {
      const header = request.headers()["authorization"];
      if (header?.startsWith("Bearer ")) bearer = header;
    }
  };
  page.on("request", capture);
  await page.goto("/");
  await page.getByLabel("Account identity", { exact: true }).fill(identity);
  await page.getByLabel("Password", { exact: true }).fill(setting("CC_LIVE_PASSWORD"));
  const discovered = page.waitForResponse(r => r.url().endsWith("/auth/profile"));
  const accountResponse = page.waitForResponse(r => r.url().endsWith("/auth/me"));
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  const response = await discovered;
  expect(response.status()).toBe(200);
  const profile = await response.json() as { profile_id: string; role: string };
  const account = await (await accountResponse).json() as { id: string; role: string };
  expect(account.role === profile.role && account.id !== profile.profile_id).toBe(true);
  await expect(page.getByText(`Your ${profile.role} profile: ${profile.profile_id}`, { exact: true })).toBeVisible();
  page.off("request", capture);
  expect(Boolean(bearer)).toBe(true);
  return { ...profile, bearer };
}

async function call(page: Page, path: string, bearer: string, body?: object) {
  return page.evaluate(async ({ path, bearer, body }) => {
    const response = await fetch(`/api/v1${path}`, {
      method: body === undefined ? "GET" : "POST", credentials: "omit", cache: "no-store",
      headers: { Authorization: bearer, ...(body ? { "Content-Type": "application/json" } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return { status: response.status, body: await response.json() };
  }, { path, bearer, body });
}

function scopeDenied(result: Awaited<ReturnType<typeof call>>, protectedMarkers: string[] = []) {
  expect(result.status).toBe(403);
  const serialized = JSON.stringify(result.body);
  // Boolean assertions redact bodies even if a regression returns clinical data in an error.
  expect(serialized === JSON.stringify({ detail: "Patient scope denied" })).toBe(true);
  expect(protectedMarkers.every(marker => !serialized.includes(marker))).toBe(true);
}

async function invite(doctor: Page, identity: string) {
  await doctor.getByLabel("Invited synthetic identity", { exact: true }).fill(identity);
  const pending = doctor.waitForResponse(r => r.url().endsWith("/patient-invitations") && r.request().method() === "POST");
  await doctor.getByRole("button", { name: "Create patient invitation", exact: true }).click();
  const response = await pending;
  expect(response.status()).toBe(201);
  const invitation = await response.json() as {
    patient_id: string; invitation_url: string; prototype_verification_code: string;
  };
  // Boolean assertions prevent secret values appearing in a mismatch diagnostic.
  expect(await doctor.getByLabel("Copyable invitation link").inputValue() === invitation.invitation_url).toBe(true);
  await doctor.getByText("Reveal separate verification code", { exact: true }).click();
  expect(await doctor.getByLabel("Copyable separate verification code").inputValue() === invitation.prototype_verification_code).toBe(true);
  return invitation;
}

async function activate(context: BrowserContext, invitation: Awaited<ReturnType<typeof invite>>, identity: string) {
  const page = await context.newPage();
  let leaked = false;
  page.on("request", request => {
    const url = new URL(request.url());
    if (url.searchParams.has("token") || (request.method() === "GET" && url.hash)) leaked = true;
  });
  const inspect = page.waitForResponse(r => r.url().endsWith("/patient-invitations/inspect"));
  await page.goto(invitation.invitation_url);
  expect((await inspect).status()).toBe(200);
  await expect(page.getByLabel("Intended synthetic identity")).toBeVisible();
  expect(new URL(page.url()).hash === "").toBe(true);
  await page.getByLabel("Intended synthetic identity").fill(identity);
  await page.getByLabel("Separate verification code", { exact: true }).fill(invitation.prototype_verification_code);
  await page.getByLabel("New password", { exact: true }).fill(setting("CC_LIVE_PASSWORD"));
  const activation = page.waitForResponse(r => r.url().endsWith("/patient-invitations/activate"));
  await page.getByRole("button", { name: "Activate patient account", exact: true }).click();
  expect((await activation).status()).toBe(201);
  await expect(page.getByText(/Account activated\. Sign in through/)).toBeVisible();
  expect(leaked).toBe(false);
  return page;
}

async function grant(patient: Page, practitioner: string) {
  await patient.getByLabel("Practitioner profile UUID").fill(practitioner);
  const response = patient.waitForResponse(r => r.url().endsWith("/patients/me/consents") && r.request().method() === "POST");
  await patient.getByRole("button", { name: "Grant practitioner access", exact: true }).click();
  expect((await response).status()).toBe(200);
  await expect(patient.getByRole("button", { name: "Review access revocation", exact: true })).toBeEnabled();
}

async function refreshPatients(doctor: Page) {
  const response = doctor.waitForResponse(r => r.url().endsWith("/api/v1/patients"));
  await doctor.getByRole("button", { name: "Refresh authorized patients", exact: true }).click();
  expect((await response).status()).toBe(200);
  await expect(doctor.getByLabel("Select authorized patient")).toBeEnabled();
}

test("real onboarding, consent, confirmation, history, corrections, record questions, audited release and revocation", async ({ browser }) => {
  test.setTimeout(300_000); // Still bounded below the unchanged launcher's 600-second browser deadline.
  const contexts: BrowserContext[] = [];
  const newContext = async () => {
    const context = await browser.newContext(); contexts.push(context); return context;
  };
  try {
    const doctor = await (await newContext()).newPage();
    const own = await login(doctor, setting("CC_LIVE_DOCTOR"));
    expect(own.role).toBe("practitioner");
    await expect(doctor.getByText(/No currently authorized patients\./)).toBeVisible();
    const run = setting("CC_LIVE_RUN");
    const firstIdentity = `patient-1-${run}@synthetic.invalid`;
    const secondIdentity = `patient-2-${run}@synthetic.invalid`;
    const first = await invite(doctor, firstIdentity);
    const second = await invite(doctor, secondIdentity);
    const patient = await activate(await newContext(), first, firstIdentity);
    const other = await activate(await newContext(), second, secondIdentity);
    const patientAuth = await login(patient, firstIdentity);
    const otherAuth = await login(other, secondIdentity);
    expect(patientAuth.profile_id === first.patient_id).toBe(true);
    expect(otherAuth.profile_id === second.patient_id).toBe(true);
    await expect(patient.getByText("No practitioner consent relationships.", { exact: true })).toBeVisible();
    const beforeConsent = await call(doctor, `/patients/${first.patient_id}/facts`, own.bearer);
    expect(beforeConsent.status).toBe(403);
    expect("records" in beforeConsent.body || Array.isArray(beforeConsent.body)).toBe(false);
    await questionStatus(patient, first.patient_id, "What confirmed clinical information is recorded?", "NO_MATCHING_RECORDS");
    await questionStatus(patient, first.patient_id, "Should I change my medication?", "CLINICAL_DECISION_UNSUPPORTED");
    await questionStatus(patient, first.patient_id, "What is my latest recorded hba1c and glucose?", "QUESTION_UNSUPPORTED");
    await grant(patient, own.profile_id);
    await grant(other, own.profile_id);
    await refreshPatients(doctor);
    expect(await doctor.getByLabel("Select authorized patient").locator("option").count()).toBe(3);
    const forbidden = await call(patient, `/patients/${second.patient_id}/facts`, patientAuth.bearer);
    expect(forbidden.status).toBe(403);
    expect((await call(patient, `/patients/${first.patient_id}/facts`, "")).status).toBe(401);

    // Mock provider is deterministic, but extraction, DB persistence and decisions are real.
    await patient.getByRole("button", { name: "Refresh server state", exact: true }).click();
    await patient.getByLabel("Synthetic message").fill(
      "HbA1c: 7.1 % at 2026-01-15T10:00:00Z\nGlucose: 100 mg/dL at 2026-01-15T10:00:00Z"
    );
    await patient.getByRole("button", { name: "Send for extraction", exact: true }).click();
    await expect(cards(patient)).toHaveCount(2);
    await expect(accepted(patient)).toHaveCount(0);
    await questionStatus(patient, first.patient_id, "What is my latest recorded hba1c?", "NO_MATCHING_RECORDS");
    await cards(patient).filter({ hasText: "hba1c" }).getByRole("button", { name: "Confirm", exact: true }).click();
    await expect(accepted(patient)).toHaveCount(1);
    await cards(patient).filter({ hasText: "glucose" }).getByRole("button", { name: "Reject", exact: true }).click();
    await expect(cards(patient).filter({ hasText: "REJECTED" })).toHaveCount(1);
    await expect(accepted(patient)).toHaveCount(1);
    await questionStatus(patient, first.patient_id, "What is my latest recorded glucose?", "NO_MATCHING_RECORDS");

    const originalFacts = await call(patient, `/patients/${first.patient_id}/facts`, patientAuth.bearer);
    expect(originalFacts.status).toBe(200);
    const originalPatientFact = acceptedFact(originalFacts.body[0], first.patient_id);
    const originalQuestion = await questionRelease(patient, first.patient_id, "What is my latest recorded value for hba1c?", [originalPatientFact]);
    expect(originalQuestion.body.records[0].text.includes('"value":"7.1"') && originalQuestion.body.records[0].text.includes('"unit":"%"')).toBe(true);
    expect(originalPatientFact.attribution === "patient_reported" && originalPatientFact.entry_method === "LLM_EXTRACTED").toBe(true);
    const originalHistory = await openHistory(patient, originalPatientFact);
    expect(originalHistory.length === 1 && originalHistory[0].confirmed_by_id === originalPatientFact.author_id).toBe(true);
    await expect(history(patient)).toContainText("Confirmation timestamp");
    await expect(history(patient)).toContainText("Source reference (metadata only)");
    const patientRelease = patient.waitForResponse(r => r.url().endsWith("/verified-records"));
    await patient.getByRole("button", { name: "Request verified record", exact: true }).click();
    expect((await patientRelease).status()).toBe(200);
    await expect(patient.getByRole("article", { name: "Server-released record", exact: true })).toHaveCount(1);
    const patientPayload = await prepareCorrection(patient, originalPatientFact, "7.2000000000", "Synthetic patient correction");
    let patientFact = await submitCorrection(patient, originalPatientFact, patientPayload);
    await expect(patient.getByRole("article", { name: "Server-released record", exact: true })).toHaveCount(0);
    await expect(questionRecords(patient)).toHaveCount(0);
    const patientHistory = await openHistory(patient, patientFact);
    expect(patientHistory.length === 2 && JSON.stringify(patientHistory[0]) === JSON.stringify(originalHistory[0])).toBe(true);
    await expect(history(patient)).toContainText(`Version ${originalPatientFact.version} — Superseded`);
    const patientQuestion = await questionRelease(patient, first.patient_id, "What is my latest recorded value for hba1c?", [patientFact], true);
    expect(patientQuestion.body.records[0].text.includes('"value":"7.2"')).toBe(true); // Renderer normalizes decimals, not JS arithmetic.
    const questionReplay = await call(patient, `/patients/${first.patient_id}/record-questions`, patientAuth.bearer, patientQuestion.input);
    expect(questionReplay.status === 409 && JSON.stringify(questionReplay.body) === JSON.stringify({ detail: "Audited response unavailable" })).toBe(true);

    await doctor.getByLabel("Select authorized patient").selectOption(first.patient_id);
    await expect(accepted(doctor)).toHaveCount(1);
    await expect(cards(doctor)).toHaveCount(0); // Consent never shares another speaker's transcript.
    await questionRelease(doctor, first.patient_id, "What is the latest recorded hba1c?", [patientFact]);
    const released = doctor.waitForResponse(r => r.url().endsWith("/verified-records"));
    await doctor.getByRole("button", { name: "Request verified record", exact: true }).click();
    const response = await released;
    expect(response.status()).toBe(200);
    const body = await response.body();
    const request = response.request().postDataJSON() as { operation_id: string; request: object };
    const record = JSON.parse(body.toString()) as { records: { text: string }[] };
    await expect(doctor.getByRole("article", { name: "Server-released record", exact: true })).toHaveCount(1);
    expect(await doctor.getByRole("article", { name: "Server-released record", exact: true }).textContent().then(t => t?.includes(record.records[0].text))).toBe(true);
    const digest = createHash("sha256").update(body).digest("hex");
    const observer = spawnSync(setting("CC_LIVE_PYTHON"), [
      "-B", "-m", "tests.live_support", "observe", "--operation", request.operation_id, "--digest", digest,
    ], { cwd: setting("CC_LIVE_BACKEND"), env: process.env, encoding: "utf8", timeout: 15_000, windowsHide: true });
    expect(observer.status === 0).toBe(true);
    const metadata = JSON.parse(observer.stdout);
    expect(metadata.events === 1 && metadata.intents === 1 && metadata.pass && metadata.digest_matches).toBe(true);
    const replay = await call(doctor, `/patients/${first.patient_id}/verified-records`, own.bearer, request);
    expect(replay.status).toBe(409);
    expect("records" in replay.body).toBe(false);

    // A separate practitioner conversation creates only practitioner-managed accepted information.
    await doctor.getByLabel("Synthetic message").fill("HbA1c: 6.8 % at 2026-01-16T10:00:00Z");
    await doctor.getByRole("button", { name: "Send for extraction", exact: true }).click();
    await expect(cards(doctor)).toHaveCount(1);
    await expect(cards(doctor)).toContainText("Practitioner-managed");
    await cards(doctor).getByRole("button", { name: "Confirm", exact: true }).click();
    await expect(accepted(doctor)).toHaveCount(2);
    const doctorFacts = await call(doctor, `/patients/${first.patient_id}/facts`, own.bearer);
    expect(doctorFacts.status).toBe(200);
    let doctorFact = acceptedFact(doctorFacts.body.find((row: AcceptedFact) => row.attribution === "practitioner_managed"), first.patient_id);
    await expect(factCard(doctor, patientFact).getByRole("button", { name: "Correct record", exact: true })).toHaveCount(0);
    // Reacquire a real released restatement; extraction itself already invalidated the earlier display.
    const doctorRelease = doctor.waitForResponse(r => r.url().endsWith("/verified-records"));
    await doctor.getByRole("button", { name: "Request verified record", exact: true }).click();
    expect((await doctorRelease).status()).toBe(200);
    await expect(doctor.getByRole("article", { name: "Server-released record", exact: true })).toHaveCount(1);
    const doctorOriginal = doctorFact;
    const doctorPayload = await prepareCorrection(doctor, doctorFact, "6.9000000000", "Synthetic practitioner correction");
    doctorFact = await submitCorrection(doctor, doctorFact, doctorPayload);
    await expect(doctor.getByRole("article", { name: "Server-released record", exact: true })).toHaveCount(0);
    const doctorHistory = await openHistory(doctor, doctorFact);
    expect(doctorHistory.length === 2 && doctorHistory[0].version_id === doctorOriginal.version_id &&
      doctorHistory[0].author_id === doctorOriginal.author_id && doctorHistory[0].provenance.source === doctorOriginal.provenance.source).toBe(true);
    await expect(history(doctor)).toContainText(`Version ${doctorOriginal.version} — Superseded`);
    // Both corrections use the same observation instant: never silently choose an attribution.
    await questionStatus(doctor, first.patient_id, "What is my latest recorded hba1c?", "LATEST_AMBIGUOUS");
    const clinicalQuestion = await questionRelease(doctor, first.patient_id, "What confirmed clinical information is recorded?", [patientFact, doctorFact], true);
    expect(clinicalQuestion.body.records.every(r => !r.text.includes('"code":"glucose"'))).toBe(true);

    await patient.getByRole("button", { name: "Refresh server state", exact: true }).click();
    await expect(accepted(patient)).toHaveCount(2);
    await expect(factCard(patient, doctorFact).getByRole("button", { name: "Correct record", exact: true })).toHaveCount(0);
    // Commit a competing, authorized correction after UI review, then submit the stale UI version.
    const stalePayload = await prepareCorrection(patient, patientFact, "7.3000000000", "Synthetic stale attempt");
    const concurrent = await call(patient, correctionPath(patientFact), patientAuth.bearer, {
      ...stalePayload, correction_reason: "Synthetic competing correction",
    });
    expect(concurrent.status).toBe(201);
    const concurrentFact = acceptedFact(concurrent.body, first.patient_id);
    let uiPosts = 0;
    const countCorrections = (r: import("@playwright/test").Request) => {
      if (r.url().endsWith(correctionPath(patientFact)) && r.method() === "POST") uiPosts++;
    };
    patient.on("request", countCorrections);
    try {
      const conflict = patient.waitForResponse(r => r.url().endsWith(correctionPath(patientFact)) && r.request().method() === "POST");
      await patient.getByRole("button", { name: "Submit new version", exact: true }).click();
      const conflictResponse = await conflict;
      expect(conflictResponse.status()).toBe(409);
      expect((conflictResponse.request().postDataJSON() as CorrectionRequest).expected_version_id === patientFact.version_id).toBe(true);
      await expect(patient.getByRole("status").filter({ hasText: "stale (409)" })).toBeVisible();
      await expect(history(patient).getByRole("heading", { name: `Version ${concurrentFact.version} — Current accepted version`, exact: true })).toBeVisible();
      await expect(editor(patient)).toHaveCount(0);
      const afterConflict = await openHistory(patient, concurrentFact);
      expect(afterConflict.length === patientHistory.length + 1 && uiPosts === 1).toBe(true);
      patientFact = concurrentFact;

      // Forward exactly one real POST, let it commit, then drop only its browser acknowledgement.
      const uncertainPayload = await prepareCorrection(patient, patientFact, "7.4000000000", "Synthetic uncertain acknowledgement");
      const routePath = `**${correctionPath(patientFact)}`;
      let finished!: () => void;
      const completed = new Promise<void>(resolve => { finished = resolve; });
      let upstreamStatus = 0, upstreamBody: unknown;
      await patient.route(routePath, async route => {
        let actual: Awaited<ReturnType<typeof route.fetch>> | undefined;
        try {
          actual = await route.fetch({ maxRetries: 0 });
          upstreamStatus = actual.status(); upstreamBody = await actual.json();
        } catch { /* Fixed assertions below, never raw upstream diagnostics. */ }
        finally {
          try { await route.abort("failed"); } catch { /* Browser cancellation may already have completed. */ }
          try { await actual?.dispose(); } catch { upstreamStatus = 0; } finally { finished(); }
        }
      });
      try {
        await patient.getByRole("button", { name: "Submit new version", exact: true }).click();
        await completed;
        expect(upstreamStatus).toBe(201);
        const committed = acceptedFact(upstreamBody, first.patient_id);
        expect(committed.previous_version_id === patientFact.version_id && committed.correction_reason === uncertainPayload.correction_reason).toBe(true);
        await expect(patient.getByRole("status").filter({ hasText: "acknowledgement is uncertain" })).toBeVisible();
        await expect(history(patient).getByRole("heading", { name: `Version ${committed.version} — Current accepted version`, exact: true })).toBeVisible();
        await expect(editor(patient)).toHaveCount(0);
        const checked = await openHistory(patient, committed);
        expect(checked.length === afterConflict.length + 1 && uiPosts === 2).toBe(true);
        patientFact = committed;
      } finally { await patient.unroute(routePath); }
    } finally { patient.off("request", countCorrections); }

    const outsider = await (await newContext()).newPage();
    const outsiderAuth = await login(outsider, setting("CC_LIVE_OUTSIDER"));
    const denied = await call(outsider, `/patients/${first.patient_id}/facts`, outsiderAuth.bearer);
    expect(denied.status).toBe(403);
    expect("records" in denied.body || Array.isArray(denied.body)).toBe(false);
    const protectedHistoryMarkers = ["7.4000000000", "6.9000000000", "Synthetic patient correction", "Synthetic practitioner correction"];
    scopeDenied(await call(other, `/patients/${first.patient_id}/facts/${patientFact.fact_id}/history`, otherAuth.bearer), protectedHistoryMarkers);
    scopeDenied(await call(other, correctionPath(patientFact), otherAuth.bearer, {
      ...patientPayload, expected_version_id: patientFact.version_id,
    }), protectedHistoryMarkers);
    scopeDenied(await call(outsider, correctionPath(doctorFact), outsiderAuth.bearer, {
      ...doctorPayload, expected_version_id: doctorFact.version_id,
    }), protectedHistoryMarkers);
    const questionBody = { operation_id: crypto.randomUUID(), question: "What confirmed clinical information is recorded?" };
    scopeDenied(await call(other, `/patients/${first.patient_id}/record-questions`, otherAuth.bearer, questionBody), protectedHistoryMarkers);
    scopeDenied(await call(outsider, `/patients/${first.patient_id}/record-questions`, outsiderAuth.bearer, { ...questionBody, operation_id: crypto.randomUUID() }), protectedHistoryMarkers);
    await prepareCorrection(doctor, doctorFact, "6.9500000000", "UNSUBMITTED SWITCH DRAFT");

    // Delay an actual authorized response; no fabricated JSON or authorization overrides.
    let entered!: () => void;
    let release!: () => void;
    let finished!: () => void;
    const started = new Promise<void>(resolve => { entered = resolve; });
    const gate = new Promise<void>(resolve => { release = resolve; });
    const completed = new Promise<void>(resolve => { finished = resolve; });
    const path = `**/patients/${first.patient_id}/facts`;
    await doctor.route(path, async route => {
      const actual = await route.fetch();
      entered(); await gate;
      try { await route.fulfill({ response: actual }); } catch { /* Context cancellation is expected. */ }
      finally { await actual.dispose(); finished(); }
    });
    await doctor.getByRole("button", { name: "Refresh server state", exact: true }).click();
    await started;
    await doctor.getByLabel("Select authorized patient").selectOption(second.patient_id);
    await expect(doctor.getByText(`Patient context: ${second.patient_id}`, { exact: true })).toBeVisible();
    release();
    await completed;
    await expect(accepted(doctor)).toHaveCount(0);
    await expect(doctor.getByRole("article", { name: "Server-released record", exact: true })).toHaveCount(0);
    await expect(history(doctor)).toHaveCount(0);
    await expect(editor(doctor)).toHaveCount(0);
    await expect(doctor.getByText("UNSUBMITTED SWITCH DRAFT", { exact: true })).toHaveCount(0);
    await doctor.unroute(path);

    // A normal authorized structured-entry POST, not an ORM seed or a medication/adherence claim.
    const medication = await call(other, `/patients/${second.patient_id}/facts`, otherAuth.bearer, {
      fact: { kind: "medication", medication_name: "Synthetic recorded medication", dose_value: "500.0000000000", dose_unit: "mg", frequency: "synthetic recorded schedule" },
      observed_at: "2026-01-17T10:00:00Z",
      provenance: { source: "Synthetic live medication fixture", reference: null, data_origin: "synthetic" },
    });
    expect(medication.status).toBe(201);
    const medicationFact = acceptedFact(medication.body, second.patient_id);
    await other.getByRole("button", { name: "Refresh server state", exact: true }).click();
    await expect(accepted(other)).toHaveCount(1);
    const medicationQuestion = await questionRelease(other, second.patient_id, "What medications are currently recorded?", [medicationFact], true);
    expect(medicationQuestion.body.records[0].text.includes('"medication_name":"Synthetic recorded medication"') &&
      medicationQuestion.body.records[0].text.includes('"dose_value":"500"') &&
      medicationQuestion.body.records.every(r => !r.text.includes(first.patient_id) && !r.text.includes(patientFact.version_id))).toBe(true);
    await expect(questions(other)).toContainText("Recorded medications do not establish what is currently being taken");
    await questionRelease(doctor, second.patient_id, "What medications are recorded?", [medicationFact]);

    // Switch with an actual open correction draft, before any refresh unmounts it.
    await doctor.getByLabel("Select authorized patient").selectOption(first.patient_id);
    await expect(accepted(doctor)).toHaveCount(2);
    await expect(questionRecords(doctor)).toHaveCount(0);
    await prepareCorrection(doctor, doctorFact, "6.9500000000", "UNSUBMITTED CONTEXT DRAFT");
    await delayedQuestionContext(doctor, first.patient_id, async () => {
      await doctor.getByLabel("Select authorized patient").selectOption(second.patient_id);
      await expect(doctor.getByText(`Patient context: ${second.patient_id}`, { exact: true })).toBeVisible();
    });
    await expect(doctor.getByText(`Patient context: ${second.patient_id}`, { exact: true })).toBeVisible();
    await expect(editor(doctor)).toHaveCount(0);
    await expect(history(doctor)).toHaveCount(0);
    await expect(doctor.getByText("UNSUBMITTED CONTEXT DRAFT", { exact: true })).toHaveCount(0);
    await doctor.getByLabel("Select authorized patient").selectOption(first.patient_id);
    await expect(accepted(doctor)).toHaveCount(2);
    await prepareCorrection(doctor, doctorFact, "6.9500000000", "UNSUBMITTED SIGNOUT DRAFT");
    await delayedQuestionContext(doctor, first.patient_id, async () => {
      await doctor.getByRole("button", { name: "Sign out", exact: true }).click();
      await expect(doctor.getByLabel("Password", { exact: true })).toBeVisible();
    });
    await expect(editor(doctor)).toHaveCount(0);
    await expect(history(doctor)).toHaveCount(0);
    await expect(accepted(doctor)).toHaveCount(0);
    await expect(doctor.getByLabel("Password", { exact: true })).toBeVisible();
    await expect(doctor.getByText("UNSUBMITTED SIGNOUT DRAFT", { exact: true })).toHaveCount(0);
    // New login for the existing revocation scenario; retain original bearer for denial checks.
    await login(doctor, setting("CC_LIVE_DOCTOR"));
    await doctor.getByLabel("Select authorized patient").selectOption(first.patient_id);
    await expect(accepted(doctor)).toHaveCount(2);
    expect((await openHistory(doctor, doctorFact)).length).toBe(2); // Drafts never created versions.
    await prepareCorrection(doctor, doctorFact, "6.9500000000", "UNSUBMITTED REVOKED DRAFT");
    // Same eligible practitioner in an independent unsaved session, to test question-specific denial.
    const questionDoctor = await (await newContext()).newPage();
    await login(questionDoctor, setting("CC_LIVE_DOCTOR"));
    await questionDoctor.getByLabel("Select authorized patient").selectOption(first.patient_id);
    await expect(accepted(questionDoctor)).toHaveCount(2);
    await questionRelease(questionDoctor, first.patient_id, "What confirmed clinical information is recorded?", [patientFact, doctorFact]);

    await patient.getByRole("button", { name: "Review access revocation", exact: true }).click();
    const revoked = patient.waitForResponse(r => r.url().endsWith(`/${own.profile_id}/revoke`));
    await patient.getByRole("button", { name: "Confirm revoke access", exact: true }).click();
    expect((await revoked).status()).toBe(200);
    const deniedQuestion = await askRecords(questionDoctor, first.patient_id, "What confirmed clinical information is recorded?");
    scopeDenied({ status: deniedQuestion.response.status(), body: await deniedQuestion.response.json() }, protectedHistoryMarkers);
    await expect(questionDoctor.getByRole("alert", { name: "Request error", exact: true })).toBeVisible();
    await expect(questionRecords(questionDoctor)).toHaveCount(0);
    await expect(accepted(questionDoctor)).toHaveCount(0);
    await expect(cards(questionDoctor)).toHaveCount(0);
    await expect(questions(questionDoctor)).toHaveCount(0);
    const revokedWrite = doctor.waitForResponse(r => r.url().endsWith(correctionPath(doctorFact)) && r.request().method() === "POST");
    await doctor.getByRole("button", { name: "Submit new version", exact: true }).click();
    const revokedResponse = await revokedWrite;
    scopeDenied({ status: revokedResponse.status(), body: await revokedResponse.json() }, protectedHistoryMarkers);
    await expect(doctor.getByRole("alert", { name: "Request error", exact: true })).toBeVisible();
    await expect(accepted(doctor)).toHaveCount(0);
    await expect(history(doctor)).toHaveCount(0);
    await expect(editor(doctor)).toHaveCount(0);
    await expect(doctor.getByText("UNSUBMITTED REVOKED DRAFT", { exact: true })).toHaveCount(0);
    const after = await call(doctor, `/patients/${first.patient_id}/facts`, own.bearer);
    const protectedMarkers = [record.records[0].text, "7.1", "hba1c", "mg/dL", "2026-01-15", ...protectedHistoryMarkers];
    scopeDenied(after, protectedMarkers);
    const withheld = await call(doctor, `/patients/${first.patient_id}/verified-records`, own.bearer, {
      ...request, operation_id: crypto.randomUUID(),
    });
    scopeDenied(withheld, protectedMarkers);
    scopeDenied(await call(doctor, `/patients/${first.patient_id}/record-questions`, own.bearer, {
      ...questionBody, operation_id: crypto.randomUUID(),
    }), [...protectedMarkers, ...clinicalQuestion.body.records.map(r => r.text)]);
    scopeDenied(await call(doctor, `/patients/${first.patient_id}/facts/${doctorFact.fact_id}/history`, own.bearer), protectedHistoryMarkers);
    scopeDenied(await call(doctor, correctionPath(doctorFact), own.bearer, {
      ...doctorPayload, expected_version_id: doctorFact.version_id,
    }), protectedHistoryMarkers);
    await refreshPatients(doctor);
    expect(await doctor.getByLabel("Select authorized patient").locator("option").count()).toBe(2);
    expect(await doctor.getByLabel("Select authorized patient").locator(`option[value="${first.patient_id}"]`).count()).toBe(0);
    await doctor.getByRole("button", { name: "Sign out", exact: true }).click();
    await expect(doctor.getByLabel("Password", { exact: true })).toBeVisible();
    await expect(accepted(doctor)).toHaveCount(0);
    for (const page of [doctor, patient, other, outsider, questionDoctor]) {
      expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
      expect((await page.context().cookies()).length).toBe(0);
    }
  } catch {
    // Never put Playwright diagnostics (which may include secrets or DOM text) in artifacts.
    throw new Error();
  } finally {
    for (const context of contexts) await context.close();
  }
});
