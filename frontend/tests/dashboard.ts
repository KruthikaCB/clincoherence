import { expect, Page } from "@playwright/test";

export const accepted = (page: Page) => page.locator('.cc-record-card, [role="article"][aria-label="Accepted fact"]');
export const patients = (page: Page) => page.locator(".cc-sidebar-patient");
export async function selectPatient(page: Page, id: string) {
  await page.locator(`.cc-sidebar-patient[title="${id}"]`).click();
}
export async function history(page: Page) {
  await page.getByRole("button", { name: /^(History|Clinical History)$/ }).click();
}
export async function verified(page: Page) {
  await page.getByRole("button", { name: "Verified Records", exact: true }).click();
}
export async function selected(page: Page, id: string) {
  await expect(page.locator(`.cc-sidebar-patient[title="${id}"]`)).toHaveClass(/active/);
}
