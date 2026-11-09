"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export class ApiError extends Error { constructor(public status: number) { super("Request failed"); } }

/** Shared dashboard transport. No error-body echo, persistence, redirects or retries. */
export async function request<T>(path: string, token?: string, body?: object, signal?: AbortSignal): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) controller.abort();
  const timer = setTimeout(abort, 20000);
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

export default function PatientSelector({ token, value, onSelect, onDenied }: {
  token: string; value: string; onSelect: (id: string) => void; onDenied: (status: number) => void;
}) {
  const [patients, setPatients] = useState<string[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const { begin, busy } = useDashboardTask();
  const load = useCallback(async () => {
    const task = begin(); if (!task) return;
    setPatients([]); setLoaded(false); setError("");
    try {
      const result = await request<unknown>("/patients", token, undefined, task.signal);
      if (!Array.isArray(result)) throw new Error();
      const ids = result.map(row => {
        const item = exact(row, "patient_id");
        if (typeof item.patient_id !== "string" || !uuid.test(item.patient_id)) throw new Error();
        return item.patient_id;
      });
      if (new Set(ids).size !== ids.length) throw new Error();
      if (task.current()) { setPatients(ids); setLoaded(true); }
    } catch (cause) {
      if (task.current()) { setError("Authorized patient list unavailable. Refresh explicitly; no patient access is inferred."); denied(cause, onDenied); }
    } finally { task.done(); }
  }, [begin, token, onDenied]);
  useEffect(() => { void load(); }, [load]);
  return <section className="panel" aria-label="Patient selection">
    <h2>Authorized patients</h2>
    <p>Only server-discovered patient profiles are offered. Every record request independently checks current consent.</p>
    <label>Select authorized patient<select className="w-full min-w-0 rounded border border-slate-500 bg-white p-2" value={value} disabled={busy || !loaded} onChange={e => { if (!e.target.value || patients.includes(e.target.value)) onSelect(e.target.value); }}>
      <option value="">No patient selected</option>
      {patients.map(id => <option key={id} value={id}>{id}</option>)}
    </select></label>
    {loaded && !patients.length && <p>No currently authorized patients. Invitation or activation alone does not grant access.</p>}
    <button className="secondary" disabled={busy} onClick={() => { onSelect(""); void load(); }}>{busy ? "Loading patients..." : "Refresh authorized patients"}</button>
    {error && <p className="error" role="alert" aria-label="Patient discovery error">{error}</p>}
  </section>;
}
