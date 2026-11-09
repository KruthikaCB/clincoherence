# Milestone 3B implementation notes

The approved requirements and architecture are unchanged. This increment adds extraction proposals and individual speaker decisions, with no evidence retrieval, advice, response generation, external LLM calls, frontend cards, or production identity claims.

## Source messages and provider boundary

`POST /patients/{patient_id}/extractions` accepts a client-generated stable `source_message_id`, text, and a declared `synthetic` or `de_identified` origin. The server binds the immutable message to the authenticated speaker and patient; it cannot be used to impersonate another speaker or attach text to another patient's record. These are minimal extraction source messages, not a shared conversation/transcript API. A reused message ID returns 409 without creating another batch. Declared data origin is not proof of de-identification.

The provider interface returns untrusted objects. The default deterministic mock (`LLM_PROVIDER=mock`) recognizes only complete, explicit timestamped lines such as:

```text
HbA1c: 7.1 % at 2026-01-01T10:00:00Z
Glucose: 110 mg/dL at 2026-01-02T10:00:00Z
Diagnosis: type_2_diabetes at 2026-01-01T10:00:00Z
Medication: metformin; dose: 500 mg; frequency: twice daily at 2026-01-01T10:00:00Z
Symptom: fatigue; description: synthetic report of tiredness at 2026-01-01T10:00:00Z
History: family_history; description: synthetic family history at 2026-01-01T10:00:00Z
```

Medication dose/frequency may be omitted and are never inferred. The same structural vocabulary/unit validation as Milestone 3A applies; nothing interprets a value as diagnostic or recommends a treatment. Unsupported natural language, negated statements, or statements missing an explicit observation time produce no inferred facts. Malformed/unsupported provider proposals reject the entire batch before persistence (502); no partial batch is accepted. An empty supported batch is valid and does not change clinical state.

Every proposal must match its exact source span, quote, typed content and observation time using a separate server-side grammar check. Extra/hallucinated facts, values, units, doses, timestamps, duplicate spans, and unsupported output shapes are rejected. This grounding policy is intentionally restrictive and is not a general semantic truth verifier. Supporting broader language or a real provider requires an explicitly reviewed grounding policy; source-span overlap alone is insufficient.

Input text is bounded to 16,000 characters and output to 32 individual proposals. Source spans use Unicode code-point offsets, end-exclusive. Provider work has a ten-second timeout, takes place outside patient locks and outside an open database transaction, and requires no production API key. Consent is checked before calling the provider and rechecked under the patient lock before storing anything. No raw text or provider exception/output is logged by this feature.

## Individual lifecycle and accepted state

An immutable proposal is `PENDING` until its sole immutable decision exists. The decision's primary key is the proposal ID, enforcing exactly one `CONFIRMED` or `REJECTED` decision. Initial source/proposal timestamps plus the decision form the immutable lifecycle/provenance record; there is no mutable status column to overwrite.

Only the source speaker may list, confirm, or reject their proposals, even when another practitioner has consent for the same patient. All operations require current patient scope; revoked practitioners cannot decide or view their pending items. Lists include the original speaker's items and current statuses, never another participant's source text. There are no bulk actions or proposal-edit endpoints.

Confirm revalidates source integrity, grounding, structure, types, units, speaker and current authorization. Within the existing patient-level lock, it inserts exactly one `LLM_EXTRACTED`/`CONFIRMED` clinical fact with matching author/confirmer, then its decision, in one transaction. Composite foreign keys bind the accepted version to the same patient and speaker, and the accepted-version reference is unique. Rejection inserts only a decision. Pending/rejected proposals never enter clinical fact state or replace accepted longitudinal values. Duplicate, replayed or conflicting decisions return 409. Decisions do not silently correct or merge an existing fact; accepted corrections remain the separate Milestone 3A workflow.

Database triggers reject UPDATE/DELETE of source messages, proposals and decisions. Existing fact-version immutability remains intact. This preserves provenance against normal application operations, not privileged database administration.

## Revocation and remaining limitations

Persistence, decisions and response release reuse the Milestone 3A patient-level locks, current database authorization and non-streaming `ClinicalResponse` handoff gate. No patient lock spans provider work. Confirmation serialized before revocation may commit; confirmation serialized after a committed revocation is denied. All decision mutations use one current patient scope and cannot change another patient's state.

The same acknowledgement limitation as 3A remains: a decision/fact can commit before revocation, yet its clinical acknowledgement can be withheld if revocation commits before final response release. A retry cannot create another accepted fact; it returns 409 if the speaker is again authorized, or is denied by current authorization otherwise. Network bytes already handed off cannot be retracted. Future streaming, background tasks and real providers must reacquire the release/write gate and must not reuse earlier permission snapshots.

Unpaginated speaker lists, coarse per-patient serialization, prototype practitioner eligibility, and the narrow mock/grounding vocabulary remain research limitations. Provider omission of a fact is possible; this is not a claim of extraction completeness or clinical correctness. User confirmation attests to the individual proposed fact, not to diagnostic or prescribing authority.

Migration `0005_supervised_extraction` follows `0004_clinical_facts`, adds source/proposal/decision tables and append-only triggers, and adds an accepted-fact scope key. It does not modify existing facts or consent. Database validation uses only explicit `TEST_DATABASE_URL`; missing configuration must be reported as skipped validation, never substituted with development credentials.
