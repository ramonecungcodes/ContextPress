# ContextPress — common commands. Run `make` or `make help` to list them.
# Local targets use $(PY) (activate your .venv first, or run `make install`).
# Docker targets need Docker; `deploy` is meant to run on the server.
.DEFAULT_GOAL := help

PY   ?= python
OUT  ?= dist
PORT ?= 8000

.PHONY: help install build serve drafts gen up down restart logs deploy clean

help: ## List available commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

# --- Local (Python) ---
install: ## Install build dependencies (into the active Python/venv)
	$(PY) -m pip install -r requirements.txt

build: ## Render the site into ./dist
	$(PY) build.py --out $(OUT)

serve: build ## Build, then preview at http://localhost:$(PORT)
	$(PY) -m http.server -d $(OUT) $(PORT)

drafts: ## Build including _drafts, then preview
	$(PY) build.py --out $(OUT) --drafts
	$(PY) -m http.server -d $(OUT) $(PORT)

# --- Docker ---
gen: ## Regenerate ./dist via the builder container
	docker compose run --rm build

up: ## Start nginx (serves the bind-mounted ./dist)
	docker compose up -d web

down: ## Stop nginx
	docker compose down

restart: ## Recreate nginx (e.g. after an nginx.conf change)
	docker compose up -d --force-recreate web

logs: ## Follow the nginx logs
	docker compose logs -f web

# --- Server ---
deploy: ## Pull, rebuild ./dist, ensure nginx is up (run on the VPS)
	./deploy.sh

clean: ## Remove build output
	rm -rf $(OUT)
