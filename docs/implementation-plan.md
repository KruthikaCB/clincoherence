# ClinCoherence incremental implementation plan

Authority: [approved Requirements v1.2](requirements-v1.2.md). Target design: [architecture](architecture.md). This plan prepares future work; no backend implementation, database migration execution, or API contract change is performed by this documentation task.

## Milestone 1 — PostgreSQL connectivity, database models, Alembic migrations

Goal: a tested persistence foundation for patient identity, consent, and audit, without exposing patient-record endpoints or implementing clinical logic.

Current starting point: async SQLAlchemy engine/session dependency and declarative base exist; Alembic is configured but imports no domain models and has no revisions. `/health` is an application smoke check, not proof of database connectivity.

### Incremental scope

1. **Connectivity and configuration.** Confirm local PostgreSQL availability and a dedicated disposable integration-test database using existing asyncpg/SQLAlchemy dependencies. Resolve environment-file loading relative to the backend working directory without committing credentials. Exercise a real query and session rollback/cleanup; preserve existing `/health` semantics.
2. **Minimal model design.** Prepare and implement the foundational entities below, documenting keys, relationships, constraints, and transaction boundaries before creating the initial revision. Use the two approved roles only. These are proposed persistence responsibilities, not finalized table names or API contracts.

   | Responsibility | Required invariants | Traceability |
   |---|---|---|
   | Individual account and patient record binding | Explicit Patient/Practitioner role, account activation distinct from consent, isolated patient identity | FR-01–03, FR-05 |
   | Onboarding invitation | Initiating practitioner, intended synthetic patient/account binding, expiry, protected token representation, atomic single-use redemption; no implicit consent | FR-04–05, NFR-01 |
   | Patient–practitioner consent relationship and history | Independent grant per pair, support multiple practitioners, distinguish current access from historical grants/revocations | FR-06–07, FR-14 |
   | Audit event | Attributable actor/patient/action/time/result and relevant provenance; preserved history, consistent transaction with consent mutation | FR-14, NFR-02–03 |

   Store no raw invitation secret in ordinary logs. Model existence alone does not enforce authorization: Milestone 2 must supply current-consent checks and revocation-safe access transactions. Do not prebuild conversation, clinical fact, evidence, or verifier tables in this first slice; introduce them alongside their workflows.
3. **Alembic revision.** Register implemented model metadata, generate and manually review the initial migration, and confirm its constraints match the model design. Keep schema changes migration-driven rather than creating tables implicitly at application startup. Review both upgrade and downgrade behavior; execute destructive rollback tests only against a verified disposable test database, never existing project data.
4. **Focused PostgreSQL tests.** Test connectivity, transaction rollback, foreign-key/uniqueness constraints, invalid role/status handling, independent practitioner–patient relationships, and preservation of consent/audit history. Validate fresh-database upgrade to head and metadata/schema agreement. Use actual PostgreSQL for database guarantees; do not infer them from SQLite behavior.

### Completion criteria

- A fresh disposable PostgreSQL database migrates to the reviewed head; rerunning upgrade is safe and produces no schema drift.
- Model tests demonstrate that activation creates no consent, a patient can have separate practitioner relationships, and invalid/cross-linked references are rejected by the chosen constraints.
- Required consent mutation/audit persistence can commit together or roll back together. No application endpoint is claimed to enforce consent merely because these models exist.
- Existing backend smoke tests and focused integration tests pass. Test configuration refuses to run destructive setup against a non-test database; connectivity failures produce a clear blocker rather than a false pass.
- Migration/model files contain no real credentials or patient data. Existing datasets, environments, caches, and generated files remain excluded. Do not import local datasets or modify existing databases to bootstrap tests.

### Prerequisites and decisions for this slice

- A reachable local PostgreSQL instance, suitable credentials supplied outside Git, and permission to create/use a disposable test database are required for integration validation. Availability has not been checked by this documentation task.
- Define invitation expiry configuration and successful-redemption completion semantics before finalizing lifecycle tests. Select a controlled synthetic identity-binding procedure for prototype onboarding; production identity assurance is not required for this slice.
- Review the minimal persistence design before implementation. Detailed clinical evidence/rule approval does not block these foundational models.

## Later milestones

| Milestone | Increment and boundary | Completion evidence |
|---|---|---|
| 2 — Authenticated onboarding and consent | Individual accounts, prototype-labeled synthetic identity flow, invitation redemption, separate activation/grant, per-request patient authorization. Coordinate revoke/read/write/release concurrency; tokens do not carry irrevocable grants. No emergency override or transcript sharing. | AC-01–03: denial, expiry and concurrent single use, no consent from activation, multiple practitioners, existing-session and pending-operation revocation; transactional audit tests |
| 3 — Patient state and isolated conversations | Add patient-specific conversations, accepted patient-reported/clinical facts, valid structured entry, linked correction versions and provenance. Enforce the initial permission matrix and transcript denial. | AC-05–07: cross-patient isolation, neither role overwrites the other, version/audit consistency and conflict handling; protected history tests |
| 4 — Mock extraction and individual confirmation | Add provider boundary, candidate facts, individual confirmation actions bound to their author/chat, and accepted-state transition with validation and retry safety. Prepare in-chat card integration. | AC-04 and FR-15: one confirmation accepts one fact, pending/rejected facts excluded, continuation with accepted facts, changed proposals require fresh confirmation |
| 5 — Independent verification and decision gate | Assemble all 11 stages with repeatable mock scenarios and reviewed synthetic evidence fixtures. Record state/evidence/provider/rule provenance; gate response release and recheck access. Clinical evidence and rule/outcome mappings require approval before evidence-backed activation. | AC-08–10: all four outcomes, withheld unverified drafts, no self-approval bypass, source approval checks, text-only and clinical-authority boundaries |
| 6 — Integrated Patient/Practitioner flows | Connect the existing frontend to implemented workflows: onboarding/consent, patient record/conversation context, individual chat cards, corrections and visible verification outcomes. Add UI pieces alongside earlier backend slices where needed for review. | End-to-end core AC-01–10 using controlled synthetic users; test multiple practitioners, revocation and isolation across sessions; no implication of production readiness |

Later milestones extend the initial schema through reviewed migrations rather than an all-at-once schema. Each slice includes its own authorization, audit, error-path, and concurrency tests where relevant; these protections are not postponed until UI integration.

## Delivery discipline and remaining dependencies

- Keep changes small and trace each slice to requirement IDs and acceptance criteria. Preserve working code and existing local datasets. Do not reset, overwrite user work, or apply migrations to an unidentified database.
- Use installed local dependencies first. Run tests appropriate to each slice; report unavailable PostgreSQL or test prerequisites explicitly. Documentation-only planning does not require rerunning application tests.
- Production identity/credential assurance and deployment security/retention parameters remain dependencies before their corresponding production stages.
- Source/version review and clinical check/outcome approval remain dependencies for evidence-backed verification; mock plumbing tests do not establish clinical correctness. Excluded datasets are not automatically approved or ingested.
- Cross-participant transcript access stays denied under shared-record permission. This plan introduces no transcript-sharing feature or additional product role.
- Requirements remain authoritative. README scope alignment is a separate documentation follow-up; this task updates architecture and planning only. No staging, commit, or push is part of this task.
