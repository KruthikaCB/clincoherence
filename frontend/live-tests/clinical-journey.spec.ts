import { test, expect, Page, BrowserContext } from "@playwright/test";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";

// Live network only. No fabricated API responses, saved auth state or diagnostic attachments.
const origin = "http://127.0.0.1:3010";
function setting(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error("Guarded fixture environment is missing");
  return value;
}
const accepted = (page: Page) => page.getByRole("article", { name: "Accepted fact", exact: true });
const cards = (page: Page) => page.getByRole("article", { name: "Fact confirmation", exact: true });

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

test("real onboarding, consent, confirmation, audited release and revocation", async ({ browser }) => {
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
    await cards(patient).filter({ hasText: "hba1c" }).getByRole("button", { name: "Confirm", exact: true }).click();
    await expect(accepted(patient)).toHaveCount(1);
    await cards(patient).filter({ hasText: "glucose" }).getByRole("button", { name: "Reject", exact: true }).click();
    await expect(cards(patient).filter({ hasText: "REJECTED" })).toHaveCount(1);
    await expect(accepted(patient)).toHaveCount(1);

    await doctor.getByLabel("Select authorized patient").selectOption(first.patient_id);
    await expect(accepted(doctor)).toHaveCount(1);
    await expect(cards(doctor)).toHaveCount(0); // Consent never shares another speaker's transcript.
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

    const outsider = await (await newContext()).newPage();
    const outsiderAuth = await login(outsider, setting("CC_LIVE_OUTSIDER"));
    const denied = await call(outsider, `/patients/${first.patient_id}/facts`, outsiderAuth.bearer);
    expect(denied.status).toBe(403);
    expect("records" in denied.body || Array.isArray(denied.body)).toBe(false);

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
    await doctor.unroute(path);

    await patient.getByRole("button", { name: "Review access revocation", exact: true }).click();
    const revoked = patient.waitForResponse(r => r.url().endsWith(`/${own.profile_id}/revoke`));
    await patient.getByRole("button", { name: "Confirm revoke access", exact: true }).click();
    expect((await revoked).status()).toBe(200);
    const after = await call(doctor, `/patients/${first.patient_id}/facts`, own.bearer);
    const protectedMarkers = [record.records[0].text, "7.1", "hba1c", "mg/dL", "2026-01-15"];
    scopeDenied(after, protectedMarkers);
    const withheld = await call(doctor, `/patients/${first.patient_id}/verified-records`, own.bearer, {
      ...request, operation_id: crypto.randomUUID(),
    });
    scopeDenied(withheld, protectedMarkers);
    await refreshPatients(doctor);
    expect(await doctor.getByLabel("Select authorized patient").locator("option").count()).toBe(2);
    expect(await doctor.getByLabel("Select authorized patient").locator(`option[value="${first.patient_id}"]`).count()).toBe(0);
    await doctor.getByRole("button", { name: "Sign out", exact: true }).click();
    await expect(doctor.getByLabel("Password", { exact: true })).toBeVisible();
    await expect(accepted(doctor)).toHaveCount(0);
    for (const page of [doctor, patient, other, outsider]) {
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
