# Optional real-Ollama segment — 60–90 seconds

This is a separate, provider-only segment. Keep the working mock application running unchanged. No frontend, patient record, confirmation, migration or live backend configuration is involved.

## Exact execution

Prerequisites: existing backend virtual environment, local Ollama already running on `127.0.0.1:11434`, and the previously verified `qwen3:0.6b` installed. The script does not install, download, start or reconfigure services.

In a separate PowerShell terminal:

```powershell
Set-Location 'C:\Users\kavan\clincoherence\backend'
& 'C:\Users\kavan\clincoherence\backend\.venv\Scripts\python.exe' -I -B -u 'C:\Users\kavan\clincoherence\backend\scripts\demo_ollama_readonly.py'
$LASTEXITCODE
```

Exit 0 means the actual candidate passed schema, literal grounding and the fixed-scenario checks. Exit 1 means failure/unavailability; exit 130 means cancellation. The input is fixed; do not add patient data or command arguments. `-I` ignores inherited Python configuration and user-site packages; `-B` avoids bytecode writes. The script explicitly adds its own backend root for the existing adapter imports.

Only the child process receives isolated Ollama settings. Dotenv loading is disabled, inherited application settings are cleared in that process, the unused database target is inert, and asyncpg connection/pool entry points are blocked. The script calls the existing adapter and grounding validator directly, never the database-writing extraction API. It makes one actual inference request with a 30-second deadline and no retry. The shorter deadline applies only to this script, not the running mock application.

## Speaking guide

| Time | Action and words |
|---|---|
| 0–15 s | Show the fixed input: `Patient has type 2 diabetes at 2026-02-05T09:00:00Z.` Say: “The main app stays in repeatable mock mode. This separate segment sends one fictional statement to the actual local qwen3 model.” |
| 15–45 s | Run the command. Say: “This uses the same extraction adapter and grounding validator, with database access blocked. It is an unaccepted proposal, not a diagnosis generated for a real person.” The request normally may finish sooner; the timeout bounds the presentation. |
| 45–65 s | Point to candidate, supporting quote, observation timestamp, PASS/FAIL and measured seconds. Say: “The schema checks structure; grounding checks correspondence to this source. This does not prove medical truth, completeness or clinician approval.” |
| 65–85 s | Show the explicitly HISTORICAL safety explanation. Say: “In our earlier test, the medication input contained no dose. The model invented 500 mg twice daily. Its JSON was valid, but grounding rejected the unsupported dose and withheld the result. We do not rely on reproducing a hallucination live.” |
| 85–90 s | Return to the existing mock application's prepared patients. Do not change its provider or resubmit its notes. |

The diagnosis candidate's optional description may vary. The script requires exactly one correct diagnosis, exact source quote/span and the stated timestamp; it never replaces the model's output with a canned successful candidate. The historical medication paragraph is explanatory text, clearly separated from the current real inference.

## Failure fallback

If unavailable, rejected, timed out or slow enough to distract, do not retry or alter settings during the review. Ctrl+C can cancel the request. Open this existing report in the editor:

`C:\Users\kavan\clincoherence\docs\demo-review-2026-11-17\OLLAMA_VERIFICATION_REPORT.md`

Say: “The live optional request did not complete successfully. This is our recorded November 12 verification, not output generated now.” Show its successful single-diagnosis result (2.669 seconds), the combined-note omissions and the rejected medication hallucination. Return to the already running mock application. Do not describe mock output or the report as current Ollama inference.

## Focused execution check

November 12, 2026: **one actual script run, PASS, exit code 0**. Real `qwen3:0.6b` returned one `diagnosis` / `type_2_diabetes` candidate with the exact note as its supporting quote, source span 0–52, and observation time `2026-02-05T09:00:00Z`. Schema, grounding and fixed-scenario checks passed. Measured inference plus validation time: **5.535 seconds**. No errors occurred and no retry was made. The medication paragraph was printed as historical explanation, not rerun inference.

A successful run demonstrates this bounded scenario only; it is not a reliability guarantee or a reason to switch the live application to Ollama. No frontend, patient, environment file, migration or running mock configuration was changed by this check.
