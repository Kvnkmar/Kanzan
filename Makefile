# Kanzen — Development Workflow
# Usage: make <target>
# Run `make help` to see all available targets.

SHELL := /bin/bash
PROJECT := /home/kavin/Kanzen
VENV := $(PROJECT)/.venv/bin
PYTHON := $(VENV)/python
PIP := $(VENV)/pip
MANAGE := $(PYTHON) $(PROJECT)/manage.py
CELERY := $(VENV)/celery
PM2_CONF := $(PROJECT)/ecosystem.config.js
PM2_DEV_CONF := $(PROJECT)/ecosystem.dev.config.js

# Colors
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m

.PHONY: help dev dev-stop start stop restart restart-django restart-workers restart-celery \
        migrate makemigrations migrate-check shell dbshell test test-fast lint \
        logs logs-django logs-celery logs-all status check collectstatic \
        restart-backend restart-all smoke

# ─── Help ───────────────────────────────────────────────────────────────────────

help: ## Show this help
	@echo ""
	@echo "Kanzen — Development Commands"
	@echo "────────────────────────────────────"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-22s$(NC) %s\n", $$1, $$2}'
	@echo ""

# ─── Development Mode (auto-reload) ────────────────────────────────────────────

dev: ## Start all services in dev mode (Django runserver + Celery with autoreload)
	@echo -e "$(GREEN)Starting Kanzen in development mode...$(NC)"
	@pm2 start $(PM2_DEV_CONF) --env development
	@echo -e "$(GREEN)All services started. Use 'make logs' to watch output.$(NC)"
	@echo -e "$(YELLOW)Django: http://localhost:8001  |  Flower: http://localhost:5556$(NC)"
	@pm2 list --sort name | grep kanzan

dev-stop: ## Stop all dev-mode services
	@pm2 delete $(PM2_DEV_CONF) 2>/dev/null || true
	@echo -e "$(GREEN)Dev services stopped.$(NC)"

# ─── PM2 Production Mode ───────────────────────────────────────────────────────

start: ## Start all services via PM2 (production config)
	@pm2 start $(PM2_CONF)
	@pm2 list --sort name | grep kanzan

stop: ## Stop all PM2 services
	@pm2 stop kanzan-django kanzan-celery-worker kanzan-celery-beat kanzan-flower 2>/dev/null || true
	@echo -e "$(GREEN)All services stopped.$(NC)"

restart: ## Restart all PM2 services
	@pm2 restart kanzan-django kanzan-celery-worker kanzan-celery-beat kanzan-flower
	@echo -e "$(GREEN)All services restarted.$(NC)"

status: ## Show PM2 process status
	@pm2 list --sort name | grep kanzan

# ─── Targeted Restarts ─────────────────────────────────────────────────────────

restart-django: ## Restart only Django (after Python/view/template changes)
	@pm2 restart kanzan-django
	@echo -e "$(GREEN)Django restarted.$(NC)"

restart-workers: ## Restart Celery worker + beat (after task changes)
	@pm2 restart kanzan-celery-worker kanzan-celery-beat
	@echo -e "$(GREEN)Celery worker + beat restarted.$(NC)"

restart-celery: restart-workers ## Alias for restart-workers

# ─── Scenario Commands ─────────────────────────────────────────────────────────

restart-backend: ## Backend change: restart Django only
	@echo -e "$(YELLOW)Backend change detected workflow:$(NC)"
	@pm2 restart kanzan-django
	@echo -e "$(GREEN)Django restarted. Templates auto-reload in DEBUG mode.$(NC)"

migrate: ## Run pending migrations + restart Django
	@echo -e "$(YELLOW)Running migrations...$(NC)"
	$(MANAGE) migrate --run-syncdb
	@echo -e "$(GREEN)Migrations applied.$(NC)"
	@if pm2 pid kanzan-django > /dev/null 2>&1; then \
		pm2 restart kanzan-django; \
		echo -e "$(GREEN)Django restarted after migration.$(NC)"; \
	fi

makemigrations: ## Generate new migrations for all apps
	$(MANAGE) makemigrations

migrate-check: ## Check for unapplied migrations
	$(MANAGE) showmigrations | grep -E '\[ \]' && echo -e "$(RED)Unapplied migrations found!$(NC)" || echo -e "$(GREEN)All migrations applied.$(NC)"

migrate-full: ## Generate + apply migrations + restart
	@echo -e "$(YELLOW)Full migration workflow...$(NC)"
	$(MANAGE) makemigrations
	$(MANAGE) migrate --run-syncdb
	@if pm2 pid kanzan-django > /dev/null 2>&1; then \
		pm2 restart kanzan-django; \
		echo -e "$(GREEN)Django restarted after migration.$(NC)"; \
	fi

restart-all: restart ## Alias: restart everything

# ─── Static Files ──────────────────────────────────────────────────────────────

collectstatic: ## Collect static files (production only)
	$(MANAGE) collectstatic --noinput

# ─── Database ──────────────────────────────────────────────────────────────────

shell: ## Django shell (IPython if available)
	$(MANAGE) shell

dbshell: ## Database shell
	$(MANAGE) dbshell

# ─── Testing ───────────────────────────────────────────────────────────────────

test: ## Run full test suite
	@echo -e "$(YELLOW)Running tests...$(NC)"
	cd $(PROJECT) && $(VENV)/pytest -v

test-fast: ## Run tests without slow markers (quick feedback)
	cd $(PROJECT) && $(VENV)/pytest -v -x --timeout=30

test-cov: ## Run tests with coverage report
	cd $(PROJECT) && $(VENV)/pytest -v --cov=apps --cov-report=term-missing

# ─── Code Quality ──────────────────────────────────────────────────────────────

lint: ## Lint with ruff
	$(VENV)/ruff check apps/ main/ tests/

lint-fix: ## Lint + auto-fix with ruff
	$(VENV)/ruff check --fix apps/ main/ tests/

format: ## Format with ruff
	$(VENV)/ruff format apps/ main/ tests/

# ─── Logs ───────────────────────────────────────────────────────────────────────

logs: ## Tail Django logs
	@pm2 logs kanzan-django --lines 50

logs-celery: ## Tail Celery worker logs
	@pm2 logs kanzan-celery-worker --lines 50

logs-all: ## Tail all service logs
	@pm2 logs --lines 30

# ─── Health Check ──────────────────────────────────────────────────────────────

smoke: ## Quick smoke test: check Django responds + migrations OK
	@echo -e "$(YELLOW)Running smoke test...$(NC)"
	@echo -n "  Django HTTP... "
	@curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/ | \
		grep -q "200\|301\|302" && echo -e "$(GREEN)OK$(NC)" || echo -e "$(RED)FAIL$(NC)"
	@echo -n "  Migrations... "
	@$(MANAGE) showmigrations 2>&1 | grep -q '\[ \]' && \
		echo -e "$(YELLOW)PENDING$(NC)" || echo -e "$(GREEN)OK$(NC)"
	@echo -n "  PM2 services... "
	@pm2 jlist 2>/dev/null | python3 -c "import sys,json; ps=json.load(sys.stdin); \
		kanzan=[p for p in ps if p['name'].startswith('kanzan')]; \
		online=[p for p in kanzan if p['pm2_env']['status']=='online']; \
		print(f'\033[0;32m{len(online)}/{len(kanzan)} online\033[0m') if len(online)==len(kanzan) \
		else print(f'\033[0;31m{len(online)}/{len(kanzan)} online\033[0m')" 2>/dev/null || echo "check manually"
	@echo -e "$(GREEN)Smoke test complete.$(NC)"

check: ## Full pre-commit check: lint + test + migration check
	@echo -e "$(YELLOW)Running pre-commit checks...$(NC)"
	@$(MAKE) lint
	@$(MAKE) migrate-check
	@$(MAKE) test
	@echo -e "$(GREEN)All checks passed!$(NC)"
