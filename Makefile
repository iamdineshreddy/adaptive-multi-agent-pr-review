# Project tasks. Run from the repo root.
# Requires: python >= 3.11, a venv already created (see docs/SETUP.md).

SHELL := /bin/sh
BACKEND := backend
FRONTEND := frontend

.PHONY: install test lint typecheck run-uvicorn clean frontend-install frontend-dev frontend-build

install: ## Create venv + install base and dev deps
	python -m venv .venv
	.venv/bin/python -m pip install --upgrade pip
	.venv/bin/python -m pip install -e "$(BACKEND)[dev]"

test: ## Run backend tests
	cd $(BACKEND) && ../.venv/bin/python -m pytest

lint: ## Ruff lint + format check
	cd $(BACKEND) && ../.venv/bin/python -m ruff check .
	cd $(BACKEND) && ../.venv/bin/python -m ruff format --check .

typecheck: ## mypy strict
	cd $(BACKEND) && ../.venv/bin/python -m mypy app

run-uvicorn: ## Dev server
	cd $(BACKEND) && ../.venv/bin/python -m uvicorn app.main:app --reload --port 8000

frontend-install: ## Install frontend deps
	cd $(FRONTEND) && npm install

frontend-dev: ## Run frontend dev server (proxies /api -> :8000)
	cd $(FRONTEND) && npm run dev

frontend-build: ## Type-check + production frontend build
	cd $(FRONTEND) && npm run build

clean: ## Remove caches
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache