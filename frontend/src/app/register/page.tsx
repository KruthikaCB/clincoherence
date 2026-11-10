"use client";

import { FormEvent, useEffect, useState } from "react";
import { ApiError, exact, request, useDashboardTask } from "../../components/patient-selector";

export default function RegisterPage() {
  const [phase, setPhase] = useState<"checking" | "ready" | "disabled" | "complete" | "uncertain">("checking");
  const [error, setError] = useState("");
  const task = useDashboardTask();
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const value = exact(await request<unknown>("/auth/demo-registration", undefined, undefined, controller.signal), "enabled");
        if (!controller.signal.aborted) setPhase(value.enabled === true ? "ready" : "disabled");
      } catch { if (!controller.signal.aborted) setPhase("disabled"); }
    })();
    return () => controller.abort();
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (phase !== "ready") return;
    const operation = task.begin();
    if (!operation) return;
    const form = event.currentTarget;
    const fields = new FormData(form);
    const identity = String(fields.get("identity")).trim().toLowerCase();
    const password = String(fields.get("password"));
    const password_confirmation = String(fields.get("password_confirmation"));
    const acknowledged = fields.get("acknowledge") === "on";
    form.reset(); setError("");
    try {
      if (!/^[a-z0-9][a-z0-9._+-]{0,63}@synthetic\.invalid$/.test(identity) ||
          Array.from(password).length < 8 || Array.from(password).length > 1024 ||
          password !== password_confirmation || !acknowledged) {
        setError("Use a synthetic.invalid identity, matching passwords of 8–1024 characters, and acknowledge demo limitations.");
        return;
      }
      const result = exact(await request<unknown>("/auth/demo-registration", undefined,
        { identity, password, password_confirmation, acknowledge_unverified_demo: true }, operation.signal), "identity_assurance,status");
      if (!operation.current()) return;
      if (result.status !== "created" || result.identity_assurance !== "unverified_demo") throw new Error();
      setPhase("complete");
    } catch (cause) {
      if (!operation.current()) return;
      if (cause instanceof ApiError && cause.status === 404) setPhase("disabled");
      else if (cause instanceof ApiError && [409, 422].includes(cause.status)) {
        setError("Account cannot be created. Check your input or use ordinary sign in if the identity already exists. Existing accounts are never changed.");
      } else {
        setPhase("uncertain");
        setError("The outcome is uncertain. No automatic retry was made. Try ordinary sign in before attempting account creation again.");
      }
    } finally { operation.done(); }
  }

  return <main className="workspace">
    <header><h1>ClinCoherence</h1><p>Local synthetic research demo · T2DM</p></header>
    <section className="panel max-w-xl mx-auto" aria-label="Create demo account">
      <h2>Create Account</h2>
      <p>Practitioner demo account only. This is an <strong>unverified demo identity</strong>, not professional credential verification or production registration.</p>
      <p>Patients remain invitation-only. Creating an account grants no patient access; explicit patient consent is still required.</p>
      {phase === "checking" && <p role="status">Checking local registration availability…</p>}
      {phase === "disabled" && <p role="status">Registration is disabled. An operator must explicitly enable local development/demo registration on the backend.</p>}
      {phase === "complete" && <p role="status">Demo account created. Sign in normally; no automatic login or patient consent was created.</p>}
      {error && <p role="alert" aria-label="Registration error" className="error">{error}</p>}
      {phase === "ready" && <form onSubmit={submit} aria-busy={task.busy}>
        <label>Synthetic account identity<input name="identity" type="email" placeholder="doctor-demo@synthetic.invalid" autoComplete="off" required maxLength={82} /></label>
        <label>Password<input name="password" type="password" autoComplete="off" required maxLength={2048} /></label>
        <label>Confirm password<input name="password_confirmation" type="password" autoComplete="off" required maxLength={2048} /></label>
        <p>Use 8–1024 characters. Secrets are cleared on submission and are not saved by this application.</p>
        <label className="flex items-start gap-3 font-normal"><input className="w-auto mt-1" name="acknowledge" type="checkbox" required />I understand this is an unverified synthetic demo practitioner, not a verified medical professional.</label>
        <button disabled={task.busy}>{task.busy ? "Creating…" : "Create demo practitioner"}</button>
      </form>}
      <p><a className="text-teal-800 underline" href="/">Back to sign in</a></p>
    </section>
  </main>;
}
