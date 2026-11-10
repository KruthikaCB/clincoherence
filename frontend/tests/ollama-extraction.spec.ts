import { expect, Page, test } from "@playwright/test";

const patient = "11111111-1111-4111-8111-111111111111";
const actor = "22222222-2222-4222-8222-222222222222";
const profile = "33333333-3333-4333-8333-333333333333";
const note = "The patient's HbA1c was 7.1 % at 2026-01-15T10:00:00Z.";

async function setup(page: Page, status: number) {
  let posts = 0;
  const items: object[] = [];
  await page.route("**/api/v1/**", route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/login")) return route.fulfill({ json: { access_token: "synthetic-token", token_type: "bearer" } });
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { id: actor, role: "practitioner" } });
    if (path.endsWith("/auth/profile")) return route.fulfill({ json: { profile_id: profile, role: "practitioner" } });
    if (path === "/api/v1/patients") return route.fulfill({ json: [{ patient_id: patient }] });
    if (path.endsWith("/facts")) return route.fulfill({ json: [] });
    if (path.endsWith("/confirmations")) return route.fulfill({ json: items });
    if (path.endsWith("/extractions")) {
      posts++;
      if (status !== 201) return route.fulfill({ status, json: { detail: "PRIVATE_PROVIDER_OUTPUT" } });
      const body = request.postDataJSON();
      expect(body.text).toBe(note);
      items.push({ confirmation_id: "44444444-4444-4444-8444-444444444444", patient_id: patient,
        source_message_id: body.source_message_id, speaker_id: actor, speaker_role: "practitioner",
        attribution: "practitioner_managed", status: "PENDING", accepted_version_id: null,
        proposed_fact: { kind: "laboratory", code: "hba1c", value: "7.1", unit: "%" },
        observed_at: "2026-01-15T10:00:00Z", source_quote: note, source_start: 0, source_end: note.length,
        source_sha256: "a".repeat(64), data_origin: "synthetic", provider: { name: "ollama", version: "qwen3-0.6b-cpu-v1" },
        grounding_version: "literal-note-proposals-v1", created_at: "2026-01-15T11:00:00Z", decided_at: null });
      return route.fulfill({ status: 201, json: { source_message_id: body.source_message_id, confirmations: items } });
    }
    return route.fulfill({ status: 404, json: {} });
  });
  await page.goto("/");
  await page.getByLabel("Account identity").fill("doctor-demo@synthetic.invalid");
  await page.getByLabel("Password", { exact: true }).fill("demo1234");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.locator(`button[title="${patient}"]`).click();
  await page.getByLabel("Clinical note", { exact: true }).fill(note);
  await page.getByRole("button", { name: "Submit note for extraction" }).click();
  return () => posts;
}

for (const status of [422, 502, 503, 504]) test(`extraction ${status} is clear, private and never replayed`, async ({ page }) => {
  const posts = await setup(page, status);
  await expect(page.locator("body")).toContainText(status === 422 ? "observation timestamp" : status === 502 ? "model output failed" : "Check Ollama");
  await expect(page.locator("body")).not.toContainText("PRIVATE_PROVIDER_OUTPUT");
  await page.waitForTimeout(100);
  expect(posts()).toBe(1);
});

test("Ollama result remains a pending individual proposal", async ({ page }) => {
  await setup(page, 201);
  await expect(page.getByText("PENDING", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /Accept/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Reject/ })).toBeVisible();
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
});
