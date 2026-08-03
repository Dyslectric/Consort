.DEFAULT_GOAL := help
SHELL := /bin/bash

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install: ## Install Python dependencies
	pip install -r requirements.txt

.PHONY: secrets
secrets: ## Generate .env with random staging secrets (will not clobber)
	@test ! -f .env || { echo ".env already exists; delete it first if you mean to regenerate"; exit 1; }
	@cp .env.example .env
	@app_secret=$$(openssl rand -hex 32); \
	 jicofo=$$(openssl rand -hex 16); \
	 jvb=$$(openssl rand -hex 16); \
	 sed -i.bak \
	   -e "s|^JWT_APP_SECRET=.*|JWT_APP_SECRET=$$app_secret|" \
	   -e "s|^JITSI_JWT_APP_SECRET=.*|JITSI_JWT_APP_SECRET=$$app_secret|" \
	   -e "s|^JICOFO_AUTH_PASSWORD=.*|JICOFO_AUTH_PASSWORD=$$jicofo|" \
	   -e "s|^JVB_AUTH_PASSWORD=.*|JVB_AUTH_PASSWORD=$$jvb|" \
	   .env && rm -f .env.bak
	@echo "wrote .env — JWT_APP_SECRET and JITSI_JWT_APP_SECRET are set to the same value"

.PHONY: modules
modules: ## Fetch the pinned jitsi-contrib Prosody modules
	./scripts/fetch-contrib-modules.sh

.PHONY: up
up: ## Start the staging stack
	docker compose up -d
	@echo
	@echo "Waiting for the web container to answer..."
	@for i in $$(seq 1 30); do \
	  curl -ksf "$${JITSI_BASE_URL:-https://localhost:8443}/config.js" >/dev/null && break; \
	  sleep 2; \
	done
	@echo "Stack is up at $${JITSI_BASE_URL:-https://localhost:8443}"

.PHONY: down
down: ## Stop the stack
	docker compose down

.PHONY: clean
clean: ## Stop the stack and delete its generated config
	docker compose down -v
	rm -rf .jitsi

.PHONY: logs
logs: ## Tail Prosody's log, which is where token rejections show up
	docker compose logs -f prosody

.PHONY: test
test: ## Run the offline test suite (no stack needed)
	python -m pytest tests/ -q --ignore=tests/test_isolation_live.py

.PHONY: check
check: ## Run the phase one isolation matrix against the running stack
	set -a && source .env && set +a && python -m jitsi_phase1 check --verbose

.PHONY: check-pytest
check-pytest: ## Same gate, as pytest, for CI
	set -a && source .env && set +a && python -m pytest tests/test_isolation_live.py -v

.PHONY: url
url: ## Print a joinable URL for eyeball testing (TENANT=... ROOM=...)
	@set -a && source .env && set +a && \
	 python -m jitsi_phase1 mint --tenant "$${TENANT:-engineering}" --room "$${ROOM:-c-phase1-probe}" --url
