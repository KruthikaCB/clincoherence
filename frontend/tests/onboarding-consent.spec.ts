import { test, expect, Page } from "@playwright/test";

const patient = "11111111-1111-4111-8111-111111111111";
const other = "22222222-2222-4222-8222-222222222222";
const actor = "33333333-3333-4333-8333-333333333333";
const practitioner = "44444444-4444-4444-8444-444444444444";
const invitation = "55555555-5555-4555-8555-555555555555";
const invitedPatient = "66666666-6666-4666-8666-666666666666";
const consentId = "77777777-7777-4777-8777-777777777777";
const token = "A".repeat(43), code = "C".repeat(32);
const expiry = new Date(Date.now() + 86400000).toISOString();
const notice = "Prototype-only synthetic-user identity verification; not production-grade. Deliver the verification code separately from the invitation link. Activation does not grant practitioner consent.";
type Relationship = { consent_id: string; practitioner_id: string; status: string; granted_at: string; revoked_at: string | null };
const relationship = (): Relationship => ({ consent_id: consentId, practitioner_id: practitioner, status: "granted", granted_at: "2026-01-01T10:00:00Z", revoked_at: null });

async function setup(page: Page, role: "patient" | "practitioner", options: { empty?: boolean; granted?: boolean; profileMismatch?: boolean } = {}) {
  const state = { requests: [] as { path: string; method: string; body: unknown }[], relationships: options.granted ? [relationship()] : [] as Relationship[], invitationStatus: "pending" };
  await page.route("**/api/v1/**", route => {
    const request = route.request(), path = new URL(request.url()).pathname, method = request.method();
    const body = method === "POST" ? request.postDataJSON() : null;
    state.requests.push({ path, method, body });
    if (path.endsWith("/auth/login")) return route.fulfill({ json: { access_token: "synthetic-token", token_type: "bearer" } });
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { id: actor, role } });
    if (path.endsWith("/auth/profile")) return route.fulfill({ json: { role: options.profileMismatch ? (role === "patient" ? "practitioner" : "patient") : role, profile_id: role === "patient" ? patient : practitioner } });
    if (path === "/api/v1/patients") return route.fulfill({ json: options.empty ? [] : [{ patient_id: patient }, { patient_id: other }] });
    if (path.endsWith("/confirmations")) return route.fulfill({ json: [] });
    if (path.endsWith("/facts")) return route.fulfill({ json: [{
      fact_id: path.includes(other) ? other : patient, version_id: path.includes(other) ? other : patient, version: 1, previous_version_id: null,
      patient_id: path.includes(other) ? other : patient, author_id: actor, author_role: role,
      attribution: role === "patient" ? "patient_reported" : "practitioner_managed", status: "CONFIRMED",
      observed_at: "2026-01-01T10:00:00Z", entered_at: "2026-01-01T11:00:00Z",
      fact: { kind: "laboratory", code: "hba1c", value: "7.1000000000", unit: "%" },
      provenance: { source: "synthetic-fixture", reference: null, data_origin: "synthetic" },
      entry_method: "STRUCTURED", correction_reason: null, confirmed_by_id: null, confirmed_at: null,
    }] });
    if (path === "/api/v1/patients/me/consents") {
      if (method === "POST") {
        expect(body).toEqual({ practitioner_id: practitioner });
        state.relationships = [relationship()];
        return route.fulfill({ json: state.relationships[0] });
      }
      return route.fulfill({ json: state.relationships });
    }
    if (path.endsWith(`/consents/${practitioner}/revoke`)) {
      expect(body).toEqual({});
      state.relationships[0] = { ...state.relationships[0], status: "revoked", revoked_at: "2026-01-02T10:00:00Z" };
      return route.fulfill({ json: state.relationships[0] });
    }
    if (path === "/api/v1/patient-invitations") {
      expect(method).toBe("POST");
      expect(body).toEqual({ intended_identity: "new.patient@synthetic.invalid" });
      return route.fulfill({ status: 201, json: { invitation_id: invitation, patient_id: invitedPatient, invitation_url: `http://localhost:3000/activate#token=${token}`, prototype_verification_code: code, expires_at: expiry, prototype_notice: notice } });
    }
    if (path.startsWith(`/api/v1/patient-invitations/${invitation}`)) {
      if (method === "POST") { expect(path).toMatch(/\/revoke$/); state.invitationStatus = "revoked"; }
      return route.fulfill({ json: { invitation_id: invitation, patient_id: invitedPatient, status: state.invitationStatus, expires_at: expiry } });
    }
    return route.fulfill({ status: 404, json: { detail: "PRIVATE" } });
  });
  await page.goto("/");
  return state;
}
async function login(page: Page) {
  await page.getByLabel("Account identity").fill("synthetic@synthetic.invalid");
  await page.getByLabel("Password", { exact: true }).fill("synthetic-password-only");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
}
async function create(page: Page) {
  await page.getByLabel("Invited synthetic identity").fill("New.Patient@synthetic.invalid");
  await page.getByRole("button", { name: "Create patient invitation", exact: true }).click();
}
const accepted = (page: Page) => page.getByRole("article", { name: "Accepted fact", exact: true });

test("patient automatically opens own profile rather than account UUID", async ({ page }) => {
  const state = await setup(page, "patient"); await login(page);
  await expect(accepted(page)).toHaveCount(1);
  await expect(page.getByRole("region", { name: "Practitioner consent", exact: true })).toBeVisible();
  expect(state.requests.some(r => r.path === `/api/v1/patients/${patient}/facts`)).toBe(true);
  expect(state.requests.some(r => r.path.includes(`/patients/${actor}/`))).toBe(false);
  await expect(page.getByLabel("Patient profile UUID", { exact: true })).toHaveCount(0);
  expect(state.requests.filter(r => r.method === "POST").map(r => r.path)).toEqual(["/api/v1/auth/login"]);
});

test("practitioner chooses only discovered profiles and sees own practitioner profile", async ({ page }) => {
  const state = await setup(page, "practitioner"); await login(page);
  const select = page.getByLabel("Select authorized patient");
  await expect(select.locator("option")).toHaveCount(3);
  await expect(page.getByText(`Your practitioner profile: ${practitioner}`, { exact: true })).toBeVisible();
  expect(state.requests.some(r => r.path.endsWith("/facts"))).toBe(false);
  await select.selectOption(patient); await expect(accepted(page)).toHaveCount(1);
  await select.selectOption(other); await expect(page.getByText(`Patient context: ${other}`, { exact: true })).toBeVisible();
  expect(state.requests.some(r => r.path.includes(`/patients/${practitioner}/`))).toBe(false);
});

test("empty authorized list does not offer invited or arbitrary patients", async ({ page }) => {
  const state = await setup(page, "practitioner", { empty: true }); await login(page);
  await expect(page.getByText(/No currently authorized patients\./)).toBeVisible();
  await expect(page.getByLabel("Select authorized patient").locator("option")).toHaveCount(1);
  await create(page);
  await expect(page.getByLabel("Copyable invitation link", { exact: true })).toHaveValue(`http://localhost:3000/activate#token=${token}`);
  state.invitationStatus = "consumed";
  await page.getByRole("button", { name: "Check invitation status", exact: true }).click();
  await expect(page.getByText("Invitation status: consumed", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Refresh authorized patients", exact: true }).click();
  await expect(page.getByText(/No currently authorized patients\./)).toBeVisible();
  expect(state.requests.some(r => r.path.includes("/consents") || r.path.endsWith("/activate"))).toBe(false);
});

test("invitation link/code are separate, status/revocation use known UUID and no storage or navigation", async ({ page }) => {
  const state = await setup(page, "practitioner"); await login(page); await create(page);
  await expect(page.getByLabel("Copyable invitation link", { exact: true })).toHaveValue(`http://localhost:3000/activate#token=${token}`);
  await page.getByText("Reveal separate verification code", { exact: true }).click();
  await expect(page.getByLabel("Copyable separate verification code", { exact: true })).toHaveValue(code);
  await page.getByRole("button", { name: "Check invitation status", exact: true }).click();
  await expect(page.getByText("Invitation status: pending", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Review invitation revocation", exact: true }).click();
  expect(state.requests.some(r => r.path.endsWith("/revoke"))).toBe(false);
  await page.getByRole("button", { name: "Revoke invitation", exact: true }).click();
  await expect(page.getByText("Invitation status: revoked", { exact: true })).toBeVisible();
  await expect(page.getByText(`/activate#token=${token}`, { exact: false })).toHaveCount(0);
  expect(state.requests.filter(r => r.path.startsWith("/api/v1/patient-invitations/")).every(r => !r.path.includes(token) && !r.path.includes(code))).toBe(true);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length, document.cookie, location.hash, location.search])).toEqual([0, 0, "", "", ""]);
});

test("patient switching and sign out discard invitation secrets", async ({ page }) => {
  await setup(page, "practitioner"); await login(page); await create(page);
  await expect(page.getByRole("region", { name: "Invitation link", exact: true })).toBeVisible();
  await page.getByLabel("Select authorized patient").selectOption(patient);
  await expect(accepted(page)).toHaveCount(1);
  await expect(page.getByRole("region", { name: "Invitation link", exact: true })).toHaveCount(0);
  await create(page); await expect(page.getByRole("region", { name: "Invitation link", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
  await expect(page.getByText(token, { exact: false })).toHaveCount(0);
});

test("consent grant, server refresh and explicit revoke never select patient or actor in body", async ({ page }) => {
  const state = await setup(page, "patient"); await login(page);
  await expect(page.getByText("No practitioner consent relationships.", { exact: true })).toBeVisible();
  await page.getByLabel("Practitioner profile UUID", { exact: true }).fill(practitioner);
  await page.getByRole("button", { name: "Grant practitioner access", exact: true }).click();
  await expect(page.getByText("Status: granted", { exact: true })).toBeVisible();
  await expect(accepted(page)).toHaveCount(0);
  await page.getByRole("button", { name: "Review access revocation", exact: true }).click();
  expect(state.requests.some(r => r.path.endsWith("/revoke"))).toBe(false);
  await page.getByRole("button", { name: "Confirm revoke access", exact: true }).click();
  await expect(page.getByText("Status: revoked", { exact: true })).toBeVisible();
  expect(state.requests.filter(r => r.method === "POST" && r.path.includes("/consents")).map(r => r.body)).toEqual([{ practitioner_id: practitioner }, {}]);
  expect(state.requests.filter(r => r.method === "GET" && r.path.endsWith("/consents")).length).toBeGreaterThanOrEqual(3);
  await expect(page.getByRole("region", { name: "Practitioner invitations", exact: true })).toHaveCount(0);
});

for (const kind of ["invitation", "consent"]) for (const failure of ["network", "timeout"]) test(`uncertain ${kind} ${failure} mutation never automatically retries`, async ({ page }) => {
  await setup(page, kind === "invitation" ? "practitioner" : "patient"); await login(page);
  if (kind === "consent") await expect(page.getByRole("button", { name: "Grant practitioner access", exact: true })).toBeEnabled();
  await page.clock.install();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let calls = 0;
  await page.route(kind === "invitation" ? "**/api/v1/patient-invitations" : "**/patients/me/consents", async route => {
    if (route.request().method() === "GET") return route.fulfill({ json: [] });
    calls++; if (failure === "timeout") await gate;
    await route.abort("failed").catch(() => {});
  });
  if (kind === "invitation") await create(page);
  else {
    const grant = page.getByRole("button", { name: "Grant practitioner access", exact: true });
    await expect(grant).toBeEnabled();
    await page.getByLabel("Practitioner profile UUID", { exact: true }).fill(practitioner); await grant.click();
  }
  await expect.poll(() => calls).toBe(1);
  if (failure === "timeout") await page.clock.fastForward(20001);
  await expect(page.getByRole("alert", { name: kind === "invitation" ? "Invitation error" : "Consent error" })).toContainText(/No automatic retry/);
  await page.clock.fastForward(200); expect(calls).toBe(1); release();
  if (kind === "consent") await expect(page.getByRole("button", { name: "Grant practitioner access", exact: true })).toBeDisabled();
});

test("malformed invitation status is withheld instead of coerced", async ({ page }) => {
  await setup(page, "practitioner"); await login(page); await create(page);
  await expect(page.getByLabel("Copyable invitation link", { exact: true })).toBeVisible();
  await page.route(`**/patient-invitations/${invitation}`, route => route.fulfill({ json: { invitation_id: invitation, patient_id: invitedPatient, status: ["pending"], expires_at: expiry } }));
  await page.getByRole("button", { name: "Check invitation status", exact: true }).click();
  await expect(page.getByRole("alert", { name: "Invitation error" })).toBeVisible();
  await expect(page.getByLabel("Copyable invitation link", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Invitation status: pending", { exact: true })).toHaveCount(0);
});

test("late discovery from signed-out patient cannot overwrite a new practitioner session", async ({ page }) => {
  await setup(page, "patient");
  let role: "patient" | "practitioner" = "patient";
  let entered!: () => void, release!: () => void;
  const started = new Promise<void>(resolve => { entered = resolve; });
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/auth/login", route => route.fulfill({ json: { access_token: role === "patient" ? "patient-token" : "practitioner-token", token_type: "bearer" } }));
  await page.route("**/auth/me", route => route.fulfill({ json: { id: role === "patient" ? actor : consentId, role } }));
  await page.route("**/auth/profile", async route => {
    if (route.request().headers().authorization === "Bearer patient-token") {
      entered(); await gate;
      await route.fulfill({ json: { role: "patient", profile_id: patient } }).catch(() => {});
    } else await route.fulfill({ json: { role: "practitioner", profile_id: practitioner } });
  });
  await login(page); await started;
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  role = "practitioner"; await login(page);
  await expect(page.getByText(`Your practitioner profile: ${practitioner}`, { exact: true })).toBeVisible(); release();
  await expect(page.getByRole("region", { name: "Practitioner consent", exact: true })).toHaveCount(0);
  await expect(accepted(page)).toHaveCount(0);
});

test("profile-role mismatch fails closed before clinical or onboarding access", async ({ page }) => {
  const state = await setup(page, "patient", { profileMismatch: true }); await login(page);
  await expect(page.getByRole("alert", { name: "Request error" })).toBeVisible();
  await expect(accepted(page)).toHaveCount(0);
  expect(state.requests.some(r => r.path.endsWith("/facts") || r.path.endsWith("/consents"))).toBe(false);
});

test("late own-profile discovery cannot restore signed-out account", async ({ page }) => {
  await setup(page, "patient");
  let entered!: () => void, release!: () => void;
  const started = new Promise<void>(resolve => { entered = resolve; });
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/auth/profile", async route => { entered(); await gate; await route.fulfill({ json: { role: "patient", profile_id: patient } }).catch(() => {}); });
  await login(page); await started;
  await page.getByRole("button", { name: "Sign out", exact: true }).click(); release();
  await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
  await expect(accepted(page)).toHaveCount(0);
});

test("late invitation creation after patient switch cannot restore secrets", async ({ page }) => {
  await setup(page, "practitioner"); await login(page);
  let entered!: () => void, release!: () => void;
  const started = new Promise<void>(resolve => { entered = resolve; });
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/v1/patient-invitations", async route => { entered(); await gate; await route.fulfill({ status: 201, json: { invitation_id: invitation, patient_id: invitedPatient, invitation_url: `http://localhost:3000/activate#token=${token}`, prototype_verification_code: code, expires_at: expiry, prototype_notice: notice } }).catch(() => {}); });
  await create(page); await started;
  await page.getByLabel("Select authorized patient").selectOption(patient);
  await expect(accepted(page)).toHaveCount(1); release();
  await expect(page.getByRole("region", { name: "Invitation link", exact: true })).toHaveCount(0);
});

test("late clinical response cannot overwrite a newly selected patient", async ({ page }) => {
  await setup(page, "practitioner"); await login(page);
  let entered!: () => void, release!: () => void;
  const started = new Promise<void>(resolve => { entered = resolve; });
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route(`**/patients/${patient}/facts`, async route => { entered(); await gate; await route.fulfill({ json: [{ patient_id: patient, status: "CONFIRMED", fact: { kind: "history", category: "other", description: "STALE PATIENT CONTENT" } }] }).catch(() => {}); });
  await page.getByLabel("Select authorized patient").selectOption(patient); await started;
  await page.getByLabel("Select authorized patient").selectOption(other);
  await expect(page.getByText(`Patient context: ${other}`, { exact: true })).toBeVisible(); release();
  await expect(page.getByText("STALE PATIENT CONTENT", { exact: true })).toHaveCount(0);
});

for (const status of [401, 403, 404]) test(`denied clinical request ${status} clears displayed clinical content`, async ({ page }) => {
  await setup(page, "practitioner"); await login(page);
  await page.getByLabel("Select authorized patient").selectOption(patient); await expect(accepted(page)).toHaveCount(1);
  await page.route(`**/patients/${patient}/facts`, route => route.fulfill({ status, json: { detail: "PRIVATE DENIAL" } }));
  await page.getByRole("button", { name: "Refresh server state", exact: true }).click();
  await expect(page.getByRole("alert", { name: "Request error" })).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(0);
  await expect(page.getByText("PRIVATE DENIAL", { exact: true })).toHaveCount(0);
});
