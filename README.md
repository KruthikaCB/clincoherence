# ClinCoherence

**Longitudinal Clinical Coherence in Multi-Turn Clinical LLM Conversations**

A model-agnostic, state-aware runtime verification layer designed to ensure longitudinal clinical coherence across multi-turn clinical conversations with large language models.

**Project Status:** Phase 0 — Foundation / Scaffolding complete (Backend scaffolding, tests, and frontend baseline verified).

---

## Planned MVP Architecture

ClinCoherence is designed as a unified clinical chatbot with two distinct operation modes:

- **Public Mode** — General health education for any user without authentication. No access to patient records or longitudinal tracking.
- **Practitioner Mode** — Authenticated clinical decision support. Authorized practitioners access patient-specific longitudinal state and runtime verification.

The initial clinical domain is **Type 2 Diabetes Mellitus (T2DM)**. The MVP is strictly **text-only**.

### Planned 11-Layer Verification Pipeline

Every message in Practitioner mode will pass through an 11-layer pipeline:

1. Authentication and authorization (Practitioner authentication; patient login is deferred)
2. Input validation (Text-only payload verification)
3. Candidate fact extraction
4. Fact validation and confirmation
5. Patient state management
6. Longitudinal event and version history
7. Clinical evidence retrieval
8. LLM response drafting (Mock LLM default during development; configurable local and API-based providers planned; real-LLM integration and validation pending)
9. Independent runtime verification
10. PASS / WARN / CLARIFY / REFUSE decision gate
11. Response and audit persistence

---

## Current Implementation State (Phase 0)

- **Backend:** FastAPI application factory with `/health` smoke test endpoint, async SQLAlchemy engine setup, Alembic migration configuration, and core Pydantic/security stubs.
- **Frontend:** Next.js 14 with TypeScript, Tailwind CSS, production build verified, and Playwright smoke test suite.
- **Data & Clinical Logic:** Not yet implemented (Phase 1+). Real patient data is strictly out of scope.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, FastAPI, SQLAlchemy 2.x (async), Alembic, Pydantic v2 |
| Frontend | Next.js 14, TypeScript, Tailwind CSS |
| Database | PostgreSQL (local) |
| Testing | pytest (backend), Playwright (frontend) |
| LLM | Mock LLM (default during development; configurable local and API-based providers planned; real-LLM integration and validation pending) |

---

## Project Structure

```
clincoherence/
├── backend/          # FastAPI backend application
│   ├── app/          # Application package (core, api, models, schemas, services)
│   ├── alembic/      # Database migrations
│   └── tests/        # Backend pytest suite
├── frontend/         # Next.js frontend application
│   ├── src/          # Application source (App Router)
│   └── tests/        # Playwright test suite
├── docs/             # Architecture and project documentation
├── .env.example      # Environment variable template
└── Makefile          # Convenience commands
```

---

## Setup Instructions

### Prerequisites

- Python 3.11 (installed and accessible via Windows Python launcher)
- Node.js 22+ & npm
- PostgreSQL (local installation)

### Backend Setup

```bash
cd backend

# On Windows (use the Python 3.11 launcher to ensure correct version):
py -3.11 -m venv .venv
.venv\Scripts\activate

# Install backend package with development dependencies:
pip install -e ".[dev]"
```

### Frontend Setup

```bash
cd frontend
npm install

# Install Playwright browser binary (Chromium):
npx playwright install chromium

# Start development server:
npm run dev
```

### Environment Configuration

Copy the sample environment file to `.env` at the project root:

```bash
# On Windows (CMD):
copy .env.example .env

# On PowerShell or Bash:
cp .env.example .env
```

---

## Development Commands

```bash
# Backend dev server
make dev-backend

# Frontend dev server
make dev-frontend

# Run all tests
make test

# Run backend tests only
make test-backend

# Run frontend tests only
make test-frontend

# Lint and format
make lint
```

---

## License

MIT
