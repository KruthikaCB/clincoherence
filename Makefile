# ========================
# ClinCoherence Makefile
# ========================

# Python paths
PYTHON_311 = C:\Users\91895\AppData\Local\Programs\Python\Python311\python.exe
VENV_PYTHON = backend\.venv\Scripts\python.exe
VENV_PIP = backend\.venv\Scripts\pip.exe
VENV_PYTEST = backend\.venv\Scripts\pytest.exe
VENV_RUFF = backend\.venv\Scripts\ruff.exe
VENV_MYPY = backend\.venv\Scripts\mypy.exe
VENV_UVICORN = backend\.venv\Scripts\uvicorn.exe
VENV_ALEMBIC = backend\.venv\Scripts\alembic.exe

.PHONY: help venv install dev-backend dev-frontend test test-backend test-frontend lint format typecheck migrate

help: ## Show this help message
	@echo Available commands:
	@echo   make venv          - Create Python 3.11 virtual environment
	@echo   make install       - Install backend dependencies
	@echo   make dev-backend   - Start FastAPI dev server
	@echo   make dev-frontend  - Start Next.js dev server
	@echo   make test          - Run all tests
	@echo   make test-backend  - Run backend tests only
	@echo   make test-frontend - Run frontend tests only
	@echo   make lint          - Lint backend code
	@echo   make format        - Format backend code
	@echo   make typecheck     - Run mypy type checking
	@echo   make migrate       - Run database migrations

venv: ## Create Python 3.11 virtual environment
	$(PYTHON_311) -m venv backend\.venv

install: ## Install backend dependencies (dev)
	$(VENV_PIP) install -e "backend\.[dev]"

dev-backend: ## Start FastAPI development server
	cd backend && ..\$(VENV_UVICORN) app.main:app --reload --host 0.0.0.0 --port 8000

dev-frontend: ## Start Next.js development server
	cd frontend && npm run dev

test: test-backend ## Run all tests

test-backend: ## Run backend pytest suite
	cd backend && ..\$(VENV_PYTEST)

test-frontend: ## Run Playwright tests
	cd frontend && npm run test

lint: ## Lint backend with ruff
	$(VENV_RUFF) check backend\app backend\tests

format: ## Format backend with ruff
	$(VENV_RUFF) format backend\app backend\tests

typecheck: ## Run mypy on backend
	cd backend && ..\$(VENV_MYPY) app

migrate: ## Run Alembic migrations (upgrade to head)
	cd backend && ..\$(VENV_ALEMBIC) upgrade head

