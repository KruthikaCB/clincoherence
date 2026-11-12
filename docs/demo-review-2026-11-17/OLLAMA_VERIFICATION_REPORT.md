# Actual Ollama verification — November 12, 2026

**Transport/integration: PASS. Complete, reliable extraction: FAIL on the tested inputs. Keep the successful live demo in mock mode.**

## Scope and isolation

This check called the existing `OllamaExtractionProvider.extract()` directly and applied the existing `validate_candidates(..., grounding_version='literal-note-proposals-v1')` validator to successful outputs. It did not call the application extraction/confirmation APIs or connect to a database. In each isolated test process, dotenv loading was disabled and `asyncpg.connect`/`create_pool` were blocked. Explicit test-only settings selected local Ollama; the running mock backend/frontend and their configuration were not changed. No migrations, model downloads, `.env` changes or disposable-cluster operations occurred.

The configured behavior described below is from the existing provider code plus explicit isolated test settings, not a claim that the running mock server was switched to Ollama.

## Local service and provider

- `http://127.0.0.1:11434/api/version`: running, version **0.40.16**.
- `/api/tags`: **qwen3:0.6b installed**, size 522,653,767 bytes, quantization `Q4_K_M`; digest `7df6b6e09427a769808717c0a93cadc4ae99ed4eb8bf5ca557c90846becea435`.
- Existing provider version: `qwen3-0.6b-cpu-v1`. Only HTTP loopback origins on port 11434 and this model name are accepted.
- Request: `/api/chat`, JSON schema from `NoteBatch`, `stream=false`, `think=false`; options `num_gpu=0`, `temperature=0`, `seed=0`, `num_ctx=8192`, `num_predict=1536`.
- Test deadline: 120 seconds; connection timeout 3 seconds; environment proxy use and redirects disabled. The provider also checks completion/model identity and caps the response body at 128 KiB.
- Results remain untrusted proposals. JSON/schema validity alone does not establish factual grounding or clinician approval.

## Main synthetic note

```text
Patient has type 2 diabetes at 2026-02-05T09:00:00Z.
HbA1c was 7.6 % at 2026-02-05T09:00:00Z.
eGFR was 42 mL/min/1.73m2 at 2026-02-05T09:00:00Z.
Patient takes metformin at 2026-02-05T09:00:00Z.
Patient denies polyuria at 2026-02-05T09:00:00Z.
Allergy status is unknown.
```

The four positive supported facts are diagnosis, HbA1c, eGFR and recorded medication. Negated polyuria and unknown allergy status must not become positive facts.

| Input | Actual main-note result |
|---|---|
| Type 2 Diabetes diagnosis | **Missing** |
| HbA1c 7.6 % | Correct laboratory candidate, exact timestamp and quote |
| eGFR 42 mL/min/1.73m2 | Correct laboratory candidate, exact timestamp and quote |
| Recorded metformin, without dose/frequency | **Missing** |
| Denied polyuria | Correctly omitted as a positive symptom |
| Unknown allergy status | Correctly omitted; no negative/normal allergy finding invented |

**2 of 4 supported positive facts extracted.** Raw `NoteBatch` schema and final application schema/grounding both passed. No incorrect or hallucinated facts appeared in this main-note result. Provider elapsed time: **9.246 seconds**. Returned quotes matched note character spans 53–93 and 94–144 respectively. There was no network, timeout or schema error.

## Targeted follow-up probes

| Probe | Actual result | Wall time |
|---|---|---:|
| Diagnosis statement alone, unchanged from the main note | One correctly grounded `type_2_diabetes` candidate; schema/grounding PASS. The optional description included the source timestamp but introduced no new clinical fact. | 2.669 s |
| Medication statement alone, unchanged from the main note | `OllamaOutputError`; no proposal released by the adapter. | 2.449 s |
| One diagnostic medication repeat, capturing raw model output | Raw JSON/schema PASS, grounding FAIL: `Medication dose not grounded`; adapter again returned `OllamaOutputError`. | 2.159 s |

The diagnostic raw medication object contained **invented `dose_value="500"`, `dose_unit="mg"`, and `frequency="twice daily"`** even though the source said only “Patient takes metformin at 2026-02-05T09:00:00Z.” These are observed hallucinated fields, not prescribing advice. The existing validator rejected the dose first and blocked the entire result; the frequency was also unsupported by the note.

Those invented fields match the existing system prompt's medication example. Example-copying is a plausible explanation, not a proven cause. No prompt, schema, grounding code or model setting was edited to conceal the failure.

Times are measured end-to-end adapter/validation wall times on this machine, not guaranteed latency or a throughput benchmark. No separate cold-start benchmark, repeated reliability evaluation or model-quality score was performed.

## Safest capstone approach

1. Keep the proven mock-mode application as the primary presentation and fallback. Do not switch it automatically.
2. If separately approved for presentation, show a short **provider-only, no-database** real-model segment using an exact successful single-statement input (for example the tested diagnosis statement), displaying input, candidate JSON, source quote, validation result and elapsed time. Clearly label it as real local inference and distinguish it from the mock UI workflow.
3. Explain the actual limitations honestly: the combined note omitted two facts, and the isolated medication probe hallucinated dose/frequency. Show that grounding rejects unsupported output; do not weaken that validator or silently replace an Ollama failure with mock output while calling it real inference.
4. Return to the unchanged mock workflow for the full patient demonstration. No live medication extraction demonstration or production-readiness claim is supported by these probes. A future approved change can investigate prompt/example copying and add relevant regression cases before another bounded Ollama rehearsal.

No patient records, application source files, environment files, Git history or running demo settings were changed. This report is the only added workspace artifact for this check. Awaiting user review before any integration change.
