"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { ActivatePatient, activated, inspection, InvitationInspection } from "../../components/onboarding-types";

type Phase = "checking" | "ready" | "submitting" | "unavailable" | "uncertain" | "complete";
const unavailable = "Invitation unavailable or verification failed. Check the separate invitation details with the issuing practitioner.";
const uncertain = "Activation outcome is uncertain. No automatic retry was made. Try ordinary sign in if activation may have completed, or contact the issuing practitioner before reopening the original invitation.";

export default function ActivatePage() {
  const [phase, setPhase] = useState<Phase>("checking");
  const [info, setInfo] = useState<InvitationInspection | null>(null);
  const [error, setError] = useState("");
  const [completedNotice, setCompletedNotice] = useState("");
  const token = useRef("");
  const captured = useRef(false);
  const live = useRef(false);
  const epoch = useRef(0);
  const pending = useRef<AbortController | null>(null);
  const form = useRef<HTMLFormElement>(null);

  useEffect(() => {
    live.current = true;
    const ticket = ++epoch.current;
    if (!captured.current) {
      captured.current = true;
      const fragment = new URLSearchParams(window.location.hash.slice(1));
      const values = fragment.getAll("token");
      // Never read a query token, put a secret in history state, or echo malformed fragments.
      token.current = Array.from(fragment.keys()).length === 1 && values.length === 1 &&
        /^[A-Za-z0-9_-]{43}$/.test(values[0]) ? values[0] : "";
      window.history.replaceState(null, "", window.location.pathname);
    }
    const current = () => live.current && epoch.current === ticket;
    // Strict Mode cleanup/setup happens before this microtask. Only the live setup inspects.
    queueMicrotask(async () => {
      if (!current()) return;
      if (!token.current) { setPhase("unavailable"); setError(unavailable); return; }
      const controller = new AbortController();
      pending.current = controller;
      const timer = setTimeout(() => controller.abort(), 20000);
      try {
        const response = await fetch("/api/v1/patient-invitations/inspect", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: token.current }), signal: controller.signal,
          cache: "no-store", credentials: "omit", redirect: "error", referrerPolicy: "no-referrer",
        });
        if (response.status !== 200) throw new Error();
        const result = inspection(await response.json());
        if (current()) {
          if (controller.signal.aborted) throw new Error();
          setInfo(result); setPhase("ready");
        }
      } catch {
        if (current()) { token.current = ""; setPhase("unavailable"); setError(unavailable); }
      } finally {
        clearTimeout(timer);
        if (current()) pending.current = null;
      }
    });
    function leave() {
      live.current = false; epoch.current++; pending.current?.abort(); token.current = "";
      form.current?.reset();
      setInfo(null); setPhase("unavailable"); setError(unavailable);
    }
    function fragmentChanged() {
      // Same-page fragment navigation does not remount React. Never keep a new
      // secret visible or silently substitute it into an existing workflow.
      window.history.replaceState(null, "", window.location.pathname);
      leave();
    }
    window.addEventListener("pagehide", leave);
    window.addEventListener("hashchange", fragmentChanged);
    return () => {
      window.removeEventListener("pagehide", leave);
      window.removeEventListener("hashchange", fragmentChanged);
      live.current = false; epoch.current = ticket + 1; pending.current?.abort(); pending.current = null;
      // Preserve the ref for Strict Mode's synchronous re-setup; erase after a real unmount.
      queueMicrotask(() => { if (!live.current) token.current = ""; });
    };
  }, []);

  async function activate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!live.current || phase !== "ready" || pending.current || !token.current) return;
    const fields = new FormData(event.currentTarget);
    const identity = String(fields.get("identity")).trim().toLowerCase();
    const verification_code = String(fields.get("verification_code"));
    const password = String(fields.get("password"));
    event.currentTarget.reset();
    if (!/^[a-z0-9][a-z0-9._+-]{0,63}@synthetic\.invalid$/.test(identity)) {
      setError("Use the intended synthetic.invalid identity supplied for this invitation."); return;
    }
    if (Array.from(password).length < 8 || Array.from(password).length > 1024) {
      setError("Password must contain 8 to 1024 characters."); return;
    }
    const controller = new AbortController();
    pending.current = controller; setPhase("submitting"); setError("");
    const ticket = epoch.current;
    const current = () => live.current && epoch.current === ticket && pending.current === controller;
    const payload: ActivatePatient = { token: token.current, identity, verification_code, password };
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch("/api/v1/patient-invitations/activate", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload), signal: controller.signal,
        cache: "no-store", credentials: "omit", redirect: "error", referrerPolicy: "no-referrer",
      });
      if (!current()) return;
      if (response.status >= 400 && response.status < 500) {
        setPhase("ready"); setError(unavailable); return;
      }
      if (response.status !== 201) throw new Error();
      const result = activated(await response.json());
      if (current()) {
        if (controller.signal.aborted) throw new Error();
        token.current = ""; setInfo(null); setCompletedNotice(result.prototype_notice); setPhase("complete");
      }
    } catch {
      if (current()) { token.current = ""; setInfo(null); setPhase("uncertain"); setError(uncertain); }
    } finally {
      clearTimeout(timer);
      if (current()) pending.current = null;
    }
  }

  return <main className="workspace">
    <header><h1>Activate your ClinCoherence account</h1><p>Synthetic-user research prototype — T2DM</p></header>
    <section className="panel" aria-label="Patient activation">
      <h2>Patient activation</h2>
      <p>Prototype-only synthetic identity verification, not production-grade identity proofing. Account activation does not grant practitioner access. Consent is a separate patient decision.</p>
      {phase === "checking" && <p role="status">Checking invitation...</p>}
      {error && <p className="error" role="alert" aria-label="Activation error">{error}</p>}
      {info && <><p>{info.prototype_notice}</p><p>Invitation expires: <time dateTime={info.expires_at}>{info.expires_at}</time></p></>}
      {(phase === "ready" || phase === "submitting") && <form ref={form} onSubmit={activate} autoComplete="off" aria-busy={phase === "submitting"}>
        <fieldset disabled={phase === "submitting"}>
          <label>Intended synthetic identity<input name="identity" type="email" required maxLength={320} autoComplete="off" autoCapitalize="none" spellCheck={false} /></label>
          <label>Separate verification code<input name="verification_code" type="password" required maxLength={128} autoComplete="off" /></label>
          <p>Obtain the verification code separately from the invitation link.</p>
          <label>New password<input name="password" type="password" required maxLength={2048} autoComplete="off" /></label>
          <p>Use 8 to 1024 characters. Password and code fields are cleared on submission.</p>
          <button>{phase === "submitting" ? "Activating..." : "Activate patient account"}</button>
        </fieldset>
      </form>}
      {phase === "complete" && <div role="status"><p>Account activated. Sign in through the ordinary login workflow. No practitioner consent has been granted.</p><p>{completedNotice}</p></div>}
      <p><a className="underline text-teal-800" href="/">Go to ordinary sign in</a></p>
    </section>
  </main>;
}
