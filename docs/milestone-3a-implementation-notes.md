# Milestone 3A implementation notes

The approved requirements and architecture remain unchanged. This increment records structured facts; it does not diagnose, prescribe, change medication, extract facts with an LLM, retrieve evidence, or generate responses.

## Data and acceptance

- Typed payloads cover a small T2DM prototype vocabulary: recorded T2DM diagnosis, medication history, HbA1c/glucose/creatinine/eGFR/UACR observations, symptoms, and relevant history. Unit labels are checked without conversion, clinical ranges, or treatment rules. Medication records describe supplied information, not prescriptions.
- Every version belongs to one patient and records observation time separately from entry time, source/provenance, author account/role, and patient-reported or practitioner-managed attribution. Caller-declared synthetic/de-identified provenance is not proof of de-identification, clinical truth, or approved evidence.
- API entries are server-marked `STRUCTURED` and `CONFIRMED` after validation and authorization. Clients cannot set authors, attribution, lifecycle, or entry method. This is accepted structured entry, not a fabricated LLM-confirmation action.
- `PENDING`, `CONFIRMED`, `REJECTED`, and `LLM_EXTRACTED` are reserved schema support. Extracted acceptance requires explicit confirmation metadata matching the speaker/author. No extraction, confirmation, or rejection API exists here. Every future extracted fact still requires individual confirmation by its appropriate speaker.
- Current state selects the latest confirmed version per fact. Pending/rejected versions cannot replace accepted values and are omitted from these clinical history endpoints. Future candidate-card/history visibility needs its own workflow.
- Corrections require `expected_version_id` and a reason. They append a linked version; a stale expected version returns 409. Composite foreign keys prevent cross-patient/cross-attribution parent links. Database triggers reject UPDATE/DELETE of fact versions. Normal API operations cannot erase history.
- Patients can read their own shared record and create/correct patient-reported information. Currently consented, eligible practitioners can read the record and create/correct practitioner-managed information. Neither role can correct the other's category. Cross-participant transcripts are not exposed.

## Revocation ordering and response boundary

Clinical operations and consent mutations acquire the same patient-profile/owner-account row locks in a PostgreSQL transaction. Authorization is checked before locking and again after obtaining the lock, using current database consent and eligibility. Lock waits are bounded to five seconds; conflicts fail closed. This assumes PostgreSQL's default READ COMMITTED isolation and all consent changes using the existing consent service.

Reads retain the patient gate through serialization and the complete, non-streaming ASGI response handoff. Consent revocation cannot commit while that gate is held. If revocation commits first, the clinical operation is denied before data release.

The database dependency explicitly uses request scope and the backend requires FastAPI >=0.121.0. This is necessary for the session/transaction to remain available through response handoff; see the [FastAPI dependency lifecycle documentation](https://fastapi.tiangolo.com/advanced/advanced-dependencies/).

Writes commit their fact version while holding the gate. Before returning any clinical body, the response reacquires the gate and rechecks authorization. If revocation commits in this acknowledgement gap, the write was committed before revocation, but its clinical acknowledgement is denied. A caller must not assume a denied/lost acknowledgement proves that no write occurred; automatic retry/idempotency is not implemented in this increment.

The final gate is released after ASGI body handoff, with a five-second handoff timeout to bound slow/non-streaming sends. This is a server-side ordering guarantee, not proof of when a remote client receives buffered network bytes. Bytes already handed off cannot be retracted. On disconnect/timeouts a response may be incomplete. Future streaming, background releases, caches, and LLM calls must not reuse an earlier authorization snapshot; perform slow work outside this gate and reacquire it for release/write.

The existing identity-only `/scope` endpoint is unchanged; clinical consumers must use the new protected clinical service/response boundary. These gates protect against consent mutations through the application; they are not tamper-proof against privileged database administration. Full audit infrastructure and production identity/credential assurance remain outside this increment.

The gate serializes clinical operations for the same patient. Current/history lists are unpaginated for the local research prototype; large-record pagination and production capacity validation remain future work. The handoff timeout does not establish an end-to-end latency guarantee.

## Validation and migrations

Migration `0004_clinical_facts` follows `0003_consent_history`, adds the fact-version table, patient lookup indexes, chain/acceptance constraints, and append-only trigger. It does not import datasets or change existing clinical/consent rows. Applying it to development is a separate explicit action.

Integration fixtures use only explicit `TEST_DATABASE_URL`, requiring a PostgreSQL test database, and create/remove random private schemas. Concurrency tests use independent database connections and exercise both revocation-first denial and clinical-handoff-first ordering. Offline checks cannot prove these PostgreSQL concurrency guarantees.
