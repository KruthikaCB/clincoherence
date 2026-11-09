# ClinCoherence Architecture

## Overview

ClinCoherence is a model-agnostic, state-aware runtime verification layer for longitudinal clinical coherence in multi-turn clinical LLM conversations.

### MVP Scope & Core Constraints
- **Text-Only MVP:** The system handles text-only clinical conversations. Audio, file attachments, and multimodal inputs are strictly out of scope.
- **Disease Domain:** The first supported domain is **Type 2 Diabetes Mellitus (T2DM)**.
- **LLM Integration:** Initial implementation utilizes a Mock LLM to establish reproducible test fixtures; local and cloud API-based LLMs will be integrated in subsequent phases.
- **Synthetic Data Only:** No real patient health information (PHI) is processed during development.

---

## Operating Modes & Access Control

### 1. Public Mode
- Available to any end user without authentication.
- Provides general health education and information only.
- Completely isolated from patient records: cannot read, create, or alter longitudinal patient state.

### 2. Practitioner Mode
- Requires authentication and granular authorization.
- **Patient Access Model:** **Patient login/portal is deferred.** Patients do not hold system accounts or credentials.
- Patient-specific longitudinal records and clinical states are managed and accessed solely by authorized practitioners.
- Integrates the full multi-turn clinical coherence verification pipeline.

---

## Core Verification Pipeline

Every turn in Practitioner mode is processed through an 11-layer architecture:

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
| **CLARIFY** | Response is withheld; system generates a clarification request to the practitioner. |
| **REFUSE** | Response is blocked due to detected safety hazards or irreconcilable contradictions. |

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
