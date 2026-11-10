"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

export const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export class ApiError extends Error { constructor(public status: number) { super("Request failed"); } }

/** Shared dashboard transport. No error-body echo, persistence, redirects or retries. */
export async function request<T>(path: string, token?: string, body?: object, signal?: AbortSignal, timeoutMilliseconds = 20000): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) controller.abort();
  const timer = setTimeout(abort, timeoutMilliseconds);
  try {
    const response = await fetch(`/api/v1${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal,
      cache: "no-store", credentials: "omit", redirect: "error", referrerPolicy: "no-referrer",
    });
    if (!response.ok) throw new ApiError(response.status);
    const result = await response.json();
    if (controller.signal.aborted) throw new Error("Request unavailable");
    return result as T;
  } finally { clearTimeout(timer); signal?.removeEventListener("abort", abort); }
}

export function exact(value: unknown, keys: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).sort().join() !== keys) throw new Error("Invalid response");
  return value as Record<string, unknown>;
}

/** One owned operation at a time, invalidated by unmount/Strict Mode cleanup. */
export function useDashboardTask() {
  const live = useRef(false);
  const generation = useRef(0);
  const pending = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    live.current = true;
    return () => { live.current = false; pending.current?.abort(); pending.current = null; };
  }, []);
  const begin = useCallback(() => {
    if (!live.current || pending.current) return null;
    const controller = new AbortController();
    const ticket = ++generation.current;
    pending.current = controller; setBusy(true);
    const current = () => live.current && generation.current === ticket && pending.current === controller;
    return { signal: controller.signal, current,
      done: () => { if (current()) { pending.current = null; setBusy(false); } } };
  }, []);
  return { begin, busy };
}

export function denied(error: unknown, onDenied: (status: number) => void): boolean {
  if (error instanceof ApiError && [401, 403, 404].includes(error.status)) { onDenied(error.status); return true; }
  return false;
}

/**
 * PatientSelector — used inside the Add Patient panel.
 * Shows the create-demo-patient form when demo mode is available,
 * and a plain patient select dropdown otherwise.
 * All selection/creation logic is preserved exactly.
 */
export default function PatientSelector({ token, value, onSelect, onDenied }: {
  token: string; value: string; onSelect: (id: string) => void; onDenied: (status: number) => void;
}) {
  const [patients, setPatients] = useState<string[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [demos, setDemos] = useState<Record<string, string> | null>(null);
  const [adding, setAdding] = useState(false);
  const { begin, busy } = useDashboardTask();

  const load = useCallback(async () => {
    const task = begin(); if (!task) return;
    setPatients([]); setDemos(null); setLoaded(false); setError("");
    try {
      const result = await request<unknown>("/patients", token, undefined, task.signal);
      if (!Array.isArray(result)) throw new Error();
      const ids = result.map(row => {
        const item = exact(row, "patient_id");
        if (typeof item.patient_id !== "string" || !uuid.test(item.patient_id)) throw new Error();
        return item.patient_id;
      });
      if (new Set(ids).size !== ids.length) throw new Error();
      let demoRows: unknown = null;
      try { demoRows = await request<unknown>("/demo-patients", token, undefined, task.signal); }
      catch (cause) { if (cause instanceof ApiError && [401, 403].includes(cause.status)) throw cause; }
      let metadata: Record<string, string> | null = null;
      if (demoRows !== null) {
        if (!Array.isArray(demoRows)) throw new Error();
        metadata = {};
        for (const row of demoRows) {
          const item = exact(row, "identifier,label,patient_id");
          if (typeof item.patient_id !== "string" || !ids.includes(item.patient_id) ||
              item.label !== "Synthetic Demo Patient" || typeof item.identifier !== "string" ||
              !/^[a-z0-9][a-z0-9._-]{0,63}$/.test(item.identifier) || metadata[item.patient_id]) throw new Error();
          metadata[item.patient_id] = item.identifier;
        }
      }
      if (task.current()) { setPatients(ids); setDemos(metadata); setLoaded(true); }
    } catch (cause) {
      if (task.current()) { setError("Authorized patient list unavailable. Refresh explicitly."); denied(cause, onDenied); }
    } finally { task.done(); }
  }, [begin, token, onDenied]);

  useEffect(() => { void load(); }, [load]);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (demos === null) return;
    const operation = begin(); if (!operation) return;
    const identifier = String(new FormData(event.currentTarget).get("identifier")).trim().toLowerCase();
    event.currentTarget.reset(); setError("");
    try {
      if (!/^[a-z0-9][a-z0-9._-]{0,63}$/.test(identifier)) throw new Error();
      const row = exact(await request<unknown>("/demo-patients", token, { identifier }, operation.signal), "identifier,label,patient_id");
      if (typeof row.patient_id !== "string" || !uuid.test(row.patient_id) || row.identifier !== identifier || row.label !== "Synthetic Demo Patient") throw new Error();
      if (!operation.current()) return;
      setPatients(previous => Array.from(new Set([...previous, row.patient_id as string])).sort());
      setDemos(previous => ({ ...previous, [row.patient_id as string]: identifier }));
      setAdding(false); onSelect(row.patient_id);
    } catch (cause) {
      if (operation.current()) {
        setError("Patient creation was not confirmed. No automatic retry — refresh authorized patients before another attempt.");
        denied(cause, onDenied);
      }
    } finally { operation.done(); }
  }

  return (
    <div aria-label="Patient selection">
      {/* Existing patients */}
      {loaded && patients.length > 0 && (
        <div style={{ marginBottom: "1rem" }}>
          <label className="cc-label">
            Select existing patient
            <select
              className="cc-select"
              value={value}
              disabled={busy || !loaded}
              onChange={e => { if (!e.target.value || patients.includes(e.target.value)) onSelect(e.target.value); }}
            >
              <option value="">— choose —</option>
              {patients.map(id => (
                <option key={id} value={id}>
                  {demos?.[id] ? `Synthetic Demo — ${demos[id]}` : id.slice(0, 8) + "…"}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      {/* Add demo patient */}
      {demos !== null && (
        <div>
          {!adding ? (
            <button
              className="cc-btn cc-btn-ghost cc-btn-sm"
              disabled={busy}
              onClick={() => setAdding(true)}
              style={{ width: "100%" }}
            >
              + Add Synthetic Demo Patient
            </button>
          ) : (
            <div className="cc-card">
              <div className="cc-card-head">
                <h3 style={{ margin: 0 }}>New synthetic demo patient</h3>
                <button className="cc-btn cc-btn-ghost cc-btn-xs" onClick={() => setAdding(false)}>Cancel</button>
              </div>
              <div className="cc-card-body">
                <div style={{ fontSize: "0.78rem", color: "#6b7280", marginBottom: "0.75rem", lineHeight: 1.5 }}>
                  Creates an accountless synthetic record — no consent, login or invitation needed.
                  Use an invented identifier; never use a real patient name.
                </div>
                <form onSubmit={create} aria-busy={busy}>
                  <label className="cc-label" style={{ marginBottom: "0.65rem" }}>
                    Synthetic patient identifier
                    <input
                      name="identifier"
                      className="cc-input"
                      placeholder="demo-patient-1"
                      autoComplete="off"
                      required
                      maxLength={64}
                      disabled={busy}
                    />
                    <span style={{ fontSize: "0.7rem", color: "#9ca3af", fontWeight: 400 }}>
                      Lowercase letters, digits, hyphens, dots. E.g. demo-patient-1
                    </span>
                  </label>
                  <button className="cc-btn cc-btn-primary cc-btn-sm" disabled={busy}>
                    {busy ? <><span className="cc-spinner" /> Creating…</> : "Create Patient"}
                  </button>
                </form>
              </div>
            </div>
          )}
        </div>
      )}

      {loaded && !patients.length && demos === null && (
        <div style={{ padding: "0.75rem", fontSize: "0.78rem", color: "#6b7280" }}>
          No authorized patients found. Demo patient creation is not available in this mode.
        </div>
      )}

      <div style={{ marginTop: "0.75rem", display: "flex", gap: "0.5rem", alignItems: "center" }}>
        <button className="cc-btn cc-btn-ghost cc-btn-xs" disabled={busy} onClick={() => { onSelect(""); void load(); }}>
          {busy ? "Loading…" : "Refresh list"}
        </button>
      </div>

      {error && <div className="cc-banner error" role="alert" style={{ marginTop: "0.65rem" }}>{error}</div>}
    </div>
  );
}
