# Makefile for chopin development tasks
# Provides convenient shortcuts for common operations

.DEFAULT_GOAL := help

PYTHON := uv run python
PYTEST := uv run pytest
CHOPIN := uv run chopin

.PHONY: help init sync lock update test test-verbose \
	fetch-lastfm fetch-spotify diff add data-dir \
	clean clean-cache clean-venv full-cleanup

help: ## Show this help message
	@echo "chopin development commands"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# === Setup ===

init: ## First-time setup: install deps and bootstrap .env from template
	@if ! command -v uv >/dev/null 2>&1; then \
		echo "error: uv is not installed. install from https://docs.astral.sh/uv/"; \
		exit 1; \
	fi
	uv sync
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo ""; \
		echo "created .env from .env.example — fill in credentials:"; \
		echo "  - last.fm api key + username (https://www.last.fm/api/account/create)"; \
		echo "  - spotify client id/secret (https://developer.spotify.com/dashboard)"; \
	else \
		echo ".env already exists, skipping copy"; \
	fi

sync: ## Re-install dependencies from lockfile
	uv sync

lock: ## Regenerate uv.lock without upgrading versions
	uv lock

update: ## Upgrade dependencies to latest compatible versions
	uv lock --upgrade
	uv sync

# === Tests ===

test: ## Run test suite
	$(PYTEST)

test-verbose: ## Run tests with verbose output
	$(PYTEST) -v

# === Application commands ===

fetch-lastfm: ## Pull full scrobble history from Last.fm
	$(CHOPIN) fetch lastfm

fetch-spotify: ## Pull a Spotify playlist (usage: make fetch-spotify [PLAYLIST=<url|uri|id|name>], default = Liked Songs)
	@if [ -z "$(PLAYLIST)" ]; then \
		$(CHOPIN) fetch spotify; \
	else \
		$(CHOPIN) fetch spotify "$(PLAYLIST)"; \
	fi

diff: ## Show tracks missing from a playlist (vars: PLAYLIST, MIN_PLAYS, EXCLUDE — for multiple excludes call chopin directly)
	@args=""; \
	if [ -n "$(MIN_PLAYS)" ]; then args="$$args --min-plays $(MIN_PLAYS)"; fi; \
	if [ -n "$(EXCLUDE)" ]; then args="$$args --exclude \"$(EXCLUDE)\""; fi; \
	if [ -z "$(PLAYLIST)" ]; then \
		eval "$(CHOPIN) diff $$args"; \
	else \
		eval "$(CHOPIN) diff \"$(PLAYLIST)\" $$args"; \
	fi

add: ## Interactively add missing tracks (usage: make add PLAYLIST=<url|uri|id>)
	@if [ -z "$(PLAYLIST)" ]; then \
		echo "error: PLAYLIST not set. usage: make add PLAYLIST=<url|uri|id>"; \
		exit 1; \
	fi
	$(CHOPIN) add "$(PLAYLIST)"

data-dir: ## Print the local cache directory path
	@$(PYTHON) -c "from chopin.storage import data_dir; print(data_dir())"

# === Cleanup ===

clean: ## Remove pytest cache, __pycache__ and build artifacts
	rm -rf .pytest_cache build dist *.egg-info src/*.egg-info
	@find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true

clean-cache: ## Remove chopin's local data cache (scrobbles, playlists, search cache, OAuth)
	@dir=$$($(PYTHON) -c "from chopin.storage import data_dir; print(data_dir())" 2>/dev/null || echo ""); \
	if [ -n "$$dir" ] && [ -d "$$dir" ]; then \
		echo "removing $$dir ..."; \
		rm -rf "$$dir"; \
		echo "done"; \
	else \
		echo "no cache directory found"; \
	fi

clean-venv: ## Remove the local virtual environment
	rm -rf .venv

full-cleanup: ## Remove venv, build artifacts and local cache (with confirmation)
	@echo "=========================================="
	@echo "Full cleanup of chopin"
	@echo "=========================================="
	@echo ""
	@echo "this will remove:"
	@echo "  - .venv/"
	@echo "  - .pytest_cache, __pycache__, build artifacts"
	@echo "  - local data cache ($$($(PYTHON) -c 'from chopin.storage import data_dir; print(data_dir())' 2>/dev/null || echo '~/.local/share/chopin/'))"
	@echo ""
	@echo ".env will NOT be removed."
	@echo ""
	@read -p "continue? [y/N]: " -n 1 -r; \
	echo; \
	if [ "$$REPLY" != "y" ] && [ "$$REPLY" != "Y" ]; then \
		echo "cancelled"; \
		exit 1; \
	fi
	@$(MAKE) clean-cache || true
	@$(MAKE) clean
	@$(MAKE) clean-venv
	@echo ""
	@echo "cleanup complete"
