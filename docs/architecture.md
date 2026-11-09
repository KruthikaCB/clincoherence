# ClinCoherence Architecture

## Overview

ClinCoherence is a model-agnostic, state-aware runtime verification layer for longitudinal clinical coherence in multi-turn clinical LLM conversations.

The authoritative MVP baseline is [Requirements v1.2](requirements-v1.2.md). This document describes the target architecture; the committed Phase 0 application remains a scaffold. The older Public/Practitioner description in README is historical until separately aligned.

### MVP Scope & Core Constraints
- **Text-Only MVP:** The system handles text-only clinical conversations. Audio, file attachments, and multimodal inputs are strictly out of scope.
- **Disease Domain:** The first supported domain is **Type 2 Diabetes Mellitus (T2DM)**.
- **LLM Integration:** Initial implementation utilizes a Mock LLM to establish reproducible test fixtures; local and cloud API-based LLMs will be integrated in subsequent phases.
- **Development Data:** Only synthetic or appropriately de-identified data, subject to provenance and suitability review. Identifiable real patient data is excluded; local datasets remain excluded from Git and are not automatically approved runtime evidence.
- **Clinical Authority:** No autonomous diagnosis, prescribing, or medication changes. Emergency consent override is excluded.

---

## Operating Modes & Access Control

### 1. Patient Mode
- Requires an authenticated, activated patient account.
- Patients access their own longitudinal record and patient-specific conversations, manage their own patient-reported facts, and individually confirm facts extracted from their own conversations.
- Patients explicitly grant or revoke access for individual practitioners. Neither activation nor invitation redemption implies consent.

### 2. Practitioner Mode
- Requires authentication and granular authorization.
- Each practitioner has an individual account. Access requires practitioner authorization and active consent from the specific patient.
- Multiple independently authorized and consented practitioners can access the same patient's shared record.
- Practitioners manage practitioner-entered clinical facts within their permissions and individually confirm facts extracted from their own conversations.
- Both authenticated modes use the full patient-specific verification pipeline. There is no unauthenticated Public clinical-chat mode in this MVP.

### Onboarding and consent lifecycle

1. An authenticated practitioner initiates patient onboarding using a copyable, expiring, single-use invitation link. Expiry and redemption are enforced server-side; concurrent redemption must yield at most one success.
2. The patient completes identity verification and account activation separately from consent. The local research MVP permits a controlled synthetic-user verification flow, visibly labeled **prototype-only, not production-grade identity verification**. Invitation possession alone is not identity proof.
3. The activated patient explicitly grants access to the identified practitioner. Only this active grant together with authorization enables record access.
4. Patients can grant access to additional practitioners or revoke individual grants. Revocation immediately denies further access, including existing authenticated sessions and pending work before patient data release or write commit. Previously delivered information cannot be retracted.

All protected operations check current role, patient scope, and consent server-side. Tokens, cached grants, and client-selected patient identifiers cannot bypass revocation. Consent-sensitive writes and response release must be coordinated with revocation to prevent a check-then-use race; the transaction/concurrency design is part of implementation preparation.

### Patient-state and conversation boundaries

Every record, conversation, retrieval, cache entry, and verifier input is scoped to its patient. Context from one patient must not enter another patient's prompt, response, or audit view. Shared-record access does not automatically grant cross-participant transcript access; that access remains denied without a separately approved explicit policy. No additional product role or override is introduced.

Patients manage their own patient-reported facts; authorized and consented practitioners manage practitioner-entered clinical facts. Neither role silently overwrites the other's information. Patient reports about clinical matters retain patient-reported provenance.

### Fact acceptance, corrections, and audit

- Extraction creates individual confirmation cards inside the originating chat. Only the conversation's author can confirm their extracted facts, subject to current access, information permissions, and validation. Confirming one card never confirms another.
- Pending/rejected proposals never enter accepted patient state or silently become factual context. Conversations may continue using previously accepted facts when all authorization and verification checks pass.
- Valid authorized structured entries can enter state without a separate extraction-confirmation step; their structured-entry provenance is retained.
- Corrections create linked versions preserving original content, original/correcting authors, timestamps, and provenance. Concurrent edits cannot silently lose earlier work. LLM-extracted corrections require individual confirmation.
- State changes, version history, and required audit events persist consistently. Grants, revocations, confirmations, corrections, and clinical interactions are attributable and auditable. Candidate/chat/audit storage is distinct from accepted state; historical audit access remains protected.

---

## Core Verification Pipeline

Patient-specific clinical turns in both authenticated modes follow the existing 11-stage architecture. Stage 1 includes current patient consent; stage 4 requires individual confirmation for extracted facts or authorization/validation for structured entries. Fact acceptance may pause while the conversation continues with previously accepted facts; no proposal bypasses confirmation into accepted context.

```
┌─────────────────────────────────────────────┐
│  1. Authentication & Authorization          │
│  2. Input Validation (Text-Only)            │
│  3. Candidate Fact Extraction               │
│  4. Fact Validation & Confirmation          │
│  5. Patient State Management                │
│  6. Longitudinal Event & Version History    │
│  7. Clinical Evidence Retrieval             │
│  8. LLM Response Drafting (Mock LLM)        │
│  9. Independent Runtime Verification        │
│ 10. Decision Gate (PASS/WARN/CLARIFY/REFUSE)│
│ 11. Response & Audit Persistence            │
└─────────────────────────────────────────────┘
```

### Decision Gate Outcomes

> [!IMPORTANT]
> **Clinical Authority & Limitations:** The system does not autonomously diagnose, prescribe, or execute medication changes. It serves as an assistive runtime verification layer; the licensed clinician retains full decision-making and prescriptive authority at all times.

| Outcome | Clinical Action |
|---|---|
| **PASS** | Response passed configured runtime verification checks (does not constitute guaranteed clinical correctness or absolute safety). |
| **WARN** | Response is presented alongside explicit clinical warning annotations. |
| **CLARIFY** | Draft answer is withheld; system requests clarification from the current patient or practitioner. |
| **REFUSE** | Response is blocked due to detected safety hazards or irreconcilable contradictions. |

### Evidence and verification boundary

The initial mock provider sits behind a model-agnostic drafting interface. Independent verification uses authorized accepted patient state/history and approved evidence; the drafting LLM's self-assessment alone is insufficient. Verification records identify the state version, draft/provider, evidence source/version/location, and verifier/rule version. Provider substitution does not bypass this boundary.

Evidence sources, versions, intended uses, clinical checks, and detailed outcome mappings require review and approval before runtime use. Terminology mappings alone are not clinical evidence; ingredient evidence does not automatically support a particular product/formulation. Preserve original values, units, and provenance without silent uncertain conversions. Missing evidence or verifier failure never defaults to PASS: withhold the unverified draft, using the approved policy to select CLARIFY or REFUSE. Recheck current access before release/persistence.

Production identity assurance is a dependency for production onboarding, not a blocker to controlled synthetic-user research. Invitation lifetime/redemption completion policy must be defined for onboarding tests. Retention and deployment security parameters require definition before deployment. See [implementation plan](implementation-plan.md) for incremental delivery and validation.

---

## Technology Stack

| Layer | Technology |
|---|---|
| Backend API | Python 3.11, FastAPI |
| ORM & Storage | SQLAlchemy 2.x (async), PostgreSQL (local) |
| Schema Migrations | Alembic |
| Data Validation | Pydantic v2 |
| Frontend | Next.js 14, TypeScript, Tailwind CSS |
| Testing | pytest, pytest-asyncio (backend); Playwright (frontend) |
| LLM Engine | Mock LLM (Phase 0/1) → Local & API-based (later) |

---

## Repository Structure

```
clincoherence/                (Git root)
├── backend/
│   ├── app/
│   │   ├── core/             Configuration, database, security
│   │   ├── models/           SQLAlchemy ORM models
│   │   ├── schemas/          Pydantic v2 schemas
│   │   ├── api/v1/           Versioned API endpoints
│   │   ├── services/         Business logic
│   │   └── middleware/       Verification, audit
│   ├── alembic/              Database migrations
│   └── tests/                Backend test suite
├── frontend/
│   ├── src/app/              Next.js App Router pages
│   └── tests/                Playwright tests
└── docs/                     Documentation
```
