.PHONY: help install test lint format typecheck run clean

# Default target
help: ## Show this help message
	@echo "DevPilot - Available Commands:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# Installation
install: ## Install all dependencies
	cd backend && pip install -r requirements.txt
	cd frontend && npm install

install-backend: ## Install backend dependencies only
	cd backend && pip install -r requirements.txt

install-frontend: ## Install frontend dependencies only
	cd frontend && npm install

# Testing
test: ## Run all tests (backend + frontend)
	cd backend && python -m pytest -q --tb=short
	cd frontend && npm test

test-backend: ## Run backend tests only
	cd backend && python -m pytest -q --tb=short

test-frontend: ## Run frontend tests only
	cd frontend && npm test

test-cov: ## Run tests with coverage
	cd backend && python -m pytest --cov=app --cov-report=html

test-live: ## Run live provider tests (requires API key)
	cd backend && python -m pytest -m live -q --tb=short

test-integration: ## Run integration tests (requires PostgreSQL)
	cd backend && python -m pytest -m integration -q --tb=short

# Code Quality
lint: ## Run linting
	cd backend && python -m ruff check app/
	cd frontend && npm run lint

format: ## Format code
	cd backend && python -m ruff format app/
	cd frontend && npx prettier --write "src/**/*.{ts,tsx}"

typecheck: ## Run type checking
	cd backend && python -m mypy app/ --ignore-missing-imports
	cd frontend && npx tsc --noEmit

# Development
run: ## Run the application (backend + frontend)
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 &
	cd frontend && npm run dev

run-backend: ## Run backend server only
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

run-frontend: ## Run frontend dev server only
	cd frontend && npm run dev

# Database
db-setup: ## Setup database (requires PostgreSQL running)
	cd backend && python -m app.db.setup_databases

db-migrate: ## Run database migrations
	cd backend && alembic upgrade head

db-check: ## Check database connectivity
	cd backend && python -m app.cli db-check

# Docker
docker-up: ## Start PostgreSQL container
	docker compose up -d

docker-down: ## Stop PostgreSQL container
	docker compose down

# CLI Commands
cli-analyze: ## Analyze a repository (usage: make cli-analyze PATH=/path/to/repo)
	cd backend && python -m app.cli analyze $(PATH)

cli-plan: ## Create implementation plan (usage: make cli-plan TASK="task description")
	cd backend && python -m app.cli plan --task "$(TASK)"

cli-run: ## Run full pipeline (usage: make cli-run REPO=/path TASK="task")
	cd backend && python -m app.cli run $(REPO) --task "$(TASK)"

cli-providers: ## List providers
	cd backend && python -m app.cli providers

cli-validate: ## Validate configuration
	cd backend && python -m app.cli validate-config

# Cleanup
clean: ## Clean build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name node_modules -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .next -exec rm -rf {} + 2>/dev/null || true
	rm -rf backend/htmlcov backend/.coverage backend/dist backend/build
	rm -rf frontend/.next frontend/out

# Documentation
docs: ## Open documentation
	@echo "Documentation files:"
	@ls -la docs/
	@echo ""
	@echo "Key documents:"
	@echo "  docs/ARCHITECTURE.md - Full pipeline architecture"
	@echo "  docs/ORCHESTRATION.md - Phase 10 orchestration"
	@echo "  docs/MULTI_PROVIDER_ROUTING.md - Provider router"
	@echo "  docs/PRODUCTION_RELIABILITY.md - Production hardening"
