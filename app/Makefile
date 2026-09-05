# The real Makefile. Runs from here or from the repo root, which proxies to it.
# scripts/dev.sh reads .worktree-env for ports, falling back to 3000/8000.

# Ports live in .worktree-env so several stacks can run side by side. dev.sh
# reads that file itself, but every other target reaches the stack through
# $$WEB_PORT / $$API_PORT -- so make has to load and export it too. Without
# this, `make probe` inside a worktree drives whichever checkout happens to own
# port 3000, which is somebody else's code. `-include` keeps a plain clone,
# which has no such file, working unchanged.
#
# Exported one at a time, and only when set: `export FOO` on an *undefined*
# make variable still puts FOO="" in the child environment, and an empty string
# is not the same as absent to everything downstream. Every shell use here
# spells the fallback `$${WEB_PORT:-3000}` and survives it, but Python does not
# -- pydantic-settings reads the empty value as one to parse and `make probe`
# died on `api_port: Input should be a valid integer`, with config.py's own
# default never consulted. `ifdef` is false for undefined *and* for empty, so
# leaving the variable unexported is what lets each consumer's default apply.
-include .worktree-env
ifdef WEB_PORT
export WEB_PORT
endif
ifdef API_PORT
export API_PORT
endif

.DEFAULT_GOAL := help
.PHONY: help setup hooks dev api web smoke probe pipeline loop generate specs gaps explore crawl trace diff runs mcp mcp-probe worktree list rm check scrub reset stop

PY_VERSION := 3.12

help: ## Show this help
	@grep -hE '^[a-z]+:.*?## ' $(MAKEFILE_LIST) | sed 's/:.*## /|/' | awk -F'|' '{printf "  %-10s %s\n", $$1, $$2}'
	@echo ""
	@echo "  worktree and rm take a name:  make worktree name=alice"

setup: ## Install all dependencies (first run only)
	cd web && npm install
	cd api && uv sync --python $(PY_VERSION)
	cd api && uv run playwright install chromium
	@$(MAKE) --no-print-directory hooks

hooks: ## Install the pre-commit check into the shared hooks dir (covers every worktree)
	@../.githooks/install.sh

dev: ## Run the full stack (web + api)
	./scripts/dev.sh

api: ## Run only the API
	cd api && uv run uvicorn app.main:app --reload --port $${API_PORT:-8000}

web: ## Run only the frontend
	cd web && npm run dev -- --port $${WEB_PORT:-3000}

smoke: ## Walking skeleton: drive a browser, break a locator, heal it
	cd api && uv run python smoke_run.py http://localhost:$${WEB_PORT:-3000}

probe: ## Observable checks for the explorer and the agents. No API key needed.
	cd api && uv run python -m agents.explorer.probe
	cd api && uv run python -m agents.probe
	cd api && uv run python -m app.probe

pipeline: ## URL in, test quality report out. No stage chosen by a human. URL=...
	cd api && uv run python -m agents.pipeline "$${URL:-http://localhost:$${WEB_PORT:-3000}/sut}"

loop: ## The whole claim, in one command: crawl, generate, run, drift, defect. URL=...
	cd api && uv run python -m agents.runner "$${URL:-http://localhost:$${WEB_PORT:-3000}/sut}"

gaps: ## Crawl an app and rank what the crawl did not cover. URL=...
	cd api && uv run python -m agents.critic "$${URL:-http://localhost:$${WEB_PORT:-3000}/sut}"

generate: ## Crawl an app and print the test plan it compiles to. URL=...
	cd api && uv run python -m agents.generator "$${URL:-http://localhost:$${WEB_PORT:-3000}/sut}"

specs: ## Write generated .spec.ts to web/generated, then run them with Playwright
	cd api && uv run python -c "$$WRITE_SPECS"
	cd web && npx playwright test generated --reporter=line

explore: ## Map an app with the agent colony. URL=... [INTENT=...]
	cd api && uv run python -m agents.orchestrator "$${URL:-http://localhost:$${WEB_PORT:-3000}/sut}" "$${INTENT}"

crawl: ## Map an app deterministically -- no model, no API key. URL=...
	cd api && uv run python -m agents.explorer.crawler "$${URL:-http://localhost:$${WEB_PORT:-3000}/sut}"

mcp: ## MCP server, for Claude Code and other agents. Needs `make dev` running.
	cd api && uv run python -m agent_mcp.server

mcp-probe: ## Observable checks for the MCP layer. Needs `make dev` running.
	cd api && uv run python -m agent_mcp.probe

trace: ## Local LLM observability UI (Phoenix) on :6006. Leave running.
	cd api && uv run python -m agents.tracing

runs: ## List saved run maps, newest last
	@ls -1t api/artifacts/runs/*.json 2>/dev/null | tail -r || echo "no runs yet"

diff: ## Compare two saved runs. BEFORE=... AFTER=...  (defaults: last two)
	@cd api && uv run python -m agents.explorer.snapshot \
		"$${BEFORE:-$$(ls -1t artifacts/runs/*.json | sed -n 2p)}" \
		"$${AFTER:-$$(ls -1t artifacts/runs/*.json | sed -n 1p)}"

worktree: ## Create a worktree with its own ports and database
	@test -n "$(name)" || { echo "usage: make worktree name=alice"; exit 1; }
	./scripts/worktree.sh new $(name)

list: ## Show every worktree, its ports, and whether it is running
	@./scripts/worktree.sh list

rm: ## Remove a worktree, keeping its branch (add force=1 if dirty)
	@test -n "$(name)" || { echo "usage: make rm name=alice [force=1]"; exit 1; }
	./scripts/worktree.sh rm $(name) $(if $(force),--force)

check: ## Typecheck and lint the frontend
	cd web && npx tsc --noEmit && npm run lint

scrub: ## Redact credentials already recorded in the database (keeps every run)
	cd api && uv run python -m agents.explorer.store

reset: ## Wipe this worktree's database and artifacts (no migrations here)
	rm -f api/app.db
	rm -rf api/artifacts/run-*
	rm -rf web/generated
	@echo "database and artifacts cleared"

stop: ## Kill this worktree's servers
	@-pkill -f "port $${WEB_PORT:-3000}" 2>/dev/null || true
	@-pkill -f "port $${API_PORT:-8000}" 2>/dev/null || true
	@echo "stopped"

# Used by `make specs`. Exported so the recipe can pass it to python -c without
# fighting make's own quoting rules.
define WRITE_SPECS
from playwright.sync_api import sync_playwright
from agents.explorer.crawler import Budget, crawl
from agents.generator import scenarios, write
import os
url = os.environ.get("URL") or f"http://localhost:{os.environ.get('WEB_PORT', '3000')}/sut"
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    world = crawl(browser.new_page(), url, Budget(max_actions=40, max_seconds=180))
    browser.close()
for path in write(scenarios(world), "../web/generated"):
    print(path)
endef
export WRITE_SPECS
