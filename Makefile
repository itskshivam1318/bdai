# Convenience wrapper. Every target works the same in the main checkout and in
# any worktree -- scripts/dev.sh reads .worktree-env for this stack's ports.

.DEFAULT_GOAL := help
.PHONY: help setup dev api web smoke worktree list rm check reset stop

PY_VERSION := 3.12

help: ## Show this help
	@grep -hE '^[a-z]+:.*?## ' $(MAKEFILE_LIST) | sed 's/:.*## /|/' | awk -F'|' '{printf "  %-10s %s\n", $$1, $$2}'
	@echo ""
	@echo "  worktree and rm take a name:  make worktree name=alice"

setup: ## Install all dependencies (first run only)
	cd web && npm install
	cd api && uv sync --python $(PY_VERSION)
	cd api && uv run playwright install chromium

dev: ## Run this worktree's full stack (web + api)
	./scripts/dev.sh

api: ## Run only the API
	cd api && uv run uvicorn app.main:app --reload --port $${API_PORT:-8000}

web: ## Run only the frontend
	cd web && npm run dev -- --port $${WEB_PORT:-3000}

smoke: ## Walking skeleton: drive a browser, break a locator, heal it
	cd api && uv run python smoke_run.py http://localhost:$${WEB_PORT:-3000}

worktree: ## Create a worktree with its own ports and database
	@test -n "$(name)" || { echo "usage: make worktree name=alice"; exit 1; }
	./scripts/worktree.sh new $(name)

list: ## Show every worktree, its ports, and whether it is running
	@./scripts/worktree.sh list

rm: ## Remove a worktree (keeps its branch)
	@test -n "$(name)" || { echo "usage: make rm name=alice"; exit 1; }
	./scripts/worktree.sh rm $(name)

check: ## Typecheck and lint the frontend
	cd web && npx tsc --noEmit && npm run lint

reset: ## Wipe this worktree's database and artifacts (no migrations here)
	rm -f api/app.db
	rm -rf api/artifacts/run-*
	@echo "database and artifacts cleared"

stop: ## Kill this worktree's servers
	@-pkill -f "port $${WEB_PORT:-3000}" 2>/dev/null || true
	@-pkill -f "port $${API_PORT:-8000}" 2>/dev/null || true
	@echo "stopped"
