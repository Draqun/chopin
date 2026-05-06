# Makefile for chopin development tasks
# Provides convenient shortcuts for common operations

.DEFAULT_GOAL := help

PYTHON := uv run python
PYTEST := uv run pytest
CHOPIN := uv run chopin

REPO_BRANCH ?= main
GIT_REMOTE := git+ssh://git@github.com/Draqun/chopin.git

.PHONY: help init sync lock update-deps test test-verbose \
	fetch-lastfm fetch-spotify diff add lonely data-dir \
	install-cli uninstall-cli update-cli build \
	clean clean-cache clean-venv full-cleanup

help: ## Show this help message
	@echo "chopin development commands"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# === Setup ===

init: ## First-time setup: create dev venv (uv sync) and bootstrap .env. Does NOT install the chopin command — see install-cli.
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

sync: ## Re-install dev dependencies from lockfile
	uv sync

lock: ## Regenerate uv.lock without upgrading versions
	uv lock

update-deps: ## Upgrade dev dependencies to latest compatible versions
	uv lock --upgrade
	uv sync

# === CLI install ===

build: ## Build a wheel into dist/
	rm -rf dist/
	uv build

install-cli: build ## Install `chopin` globally via uv tool (from local build, ~/.local/bin/chopin)
	@uv tool uninstall chopin >/dev/null 2>&1 || true
	@wheel=$$(ls -t dist/chopin-*.whl 2>/dev/null | head -1); \
	if [ -z "$$wheel" ]; then \
		echo "error: no wheel found in dist/"; exit 1; \
	fi; \
	uv tool install --force "$$wheel"
	@echo ""
	@echo "installed. run 'chopin --help' (ensure ~/.local/bin is in PATH)"

uninstall-cli: ## Remove the globally installed chopin
	@uv tool uninstall chopin 2>/dev/null || echo "chopin was not installed"

update-cli: ## Reinstall chopin globally from GitHub (REPO_BRANCH=main by default)
	uv tool install --force "chopin @ $(GIT_REMOTE)@$(REPO_BRANCH)"
	@echo "updated to latest from $(REPO_BRANCH)"

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

diff: ## Show tracks missing from a playlist (vars: PLAYLIST, MIN_PLAYS, EXCLUDE, EXCLUDE_LONELY, OUTPUT — for multiple excludes call chopin directly)
	@args=""; \
	if [ -n "$(MIN_PLAYS)" ]; then args="$$args --min-plays $(MIN_PLAYS)"; fi; \
	if [ -n "$(EXCLUDE)" ]; then args="$$args --exclude \"$(EXCLUDE)\""; fi; \
	if [ -n "$(EXCLUDE_LONELY)" ]; then args="$$args --exclude-lonely $(EXCLUDE_LONELY)"; fi; \
	if [ -n "$(OUTPUT)" ]; then args="$$args --output \"$(OUTPUT)\""; fi; \
	if [ -z "$(PLAYLIST)" ]; then \
		eval "$(CHOPIN) diff $$args"; \
	else \
		eval "$(CHOPIN) diff \"$(PLAYLIST)\" $$args"; \
	fi

lonely: ## List last.fm tracks where you are essentially the only listener (vars: MAX_LISTENERS=N)
	@if [ -z "$(MAX_LISTENERS)" ]; then \
		$(CHOPIN) lastfm lonely; \
	else \
		$(CHOPIN) lastfm lonely --max-listeners $(MAX_LISTENERS); \
	fi

add: ## Add tracks from a diff JSON to a playlist (vars: PLAYLIST, INPUT, NO_CONFIRM=1, AUTO_PICK=1, SKIPPED=<json>)
	@if [ -z "$(PLAYLIST)" ] || [ -z "$(INPUT)" ]; then \
		echo "error: PLAYLIST and INPUT are required."; \
		echo "usage: make add PLAYLIST=<...> INPUT=<diff.json> [NO_CONFIRM=1] [AUTO_PICK=1] [SKIPPED=<json>]"; \
		exit 1; \
	fi
	@args=""; \
	if [ -n "$(NO_CONFIRM)" ]; then args="$$args --no-confirm"; fi; \
	if [ -n "$(AUTO_PICK)" ]; then args="$$args --auto-pick"; fi; \
	if [ -n "$(SKIPPED)" ]; then args="$$args --skipped \"$(SKIPPED)\""; fi; \
	eval "$(CHOPIN) add \"$(PLAYLIST)\" --input \"$(INPUT)\" $$args"

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
