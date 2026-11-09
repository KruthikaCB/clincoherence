/** Existing synthetic onboarding wire contracts; never browser-persistent. */
export type InvitationSecret = { token: string };
export type ActivatePatient = InvitationSecret & {
  identity: string; verification_code: string; password: string;
};
export type InvitationInspection = {
  valid: true; expires_at: string; prototype_notice: string;
};
export type PatientActivated = {
  account_id: string; patient_id: string; prototype_notice: string;
};

function object(value: unknown, keys: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value) ||
      Object.keys(value).sort().join() !== keys) throw new Error("Invalid onboarding response");
  return value as Record<string, unknown>;
}
function notice(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0 && value.length <= 2000;
}
export function inspection(value: unknown): InvitationInspection {
  const body = object(value, "expires_at,prototype_notice,valid");
  if (body.valid !== true || !notice(body.prototype_notice) || typeof body.expires_at !== "string" ||
      !/(?:Z|[+-]\d{2}:\d{2})$/.test(body.expires_at) || !Number.isFinite(Date.parse(body.expires_at))) {
    throw new Error("Invalid onboarding response");
  }
  return { valid: true, expires_at: body.expires_at, prototype_notice: body.prototype_notice };
}
export function activated(value: unknown): PatientActivated {
  const body = object(value, "account_id,patient_id,prototype_notice");
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (!notice(body.prototype_notice) || typeof body.account_id !== "string" ||
      !uuid.test(body.account_id) || typeof body.patient_id !== "string" || !uuid.test(body.patient_id)) {
    throw new Error("Invalid onboarding response");
  }
  return { account_id: body.account_id, patient_id: body.patient_id, prototype_notice: body.prototype_notice };
}
