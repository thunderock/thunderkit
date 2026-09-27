# thunderkit — opinionated multi-model delegation for very large repos
#
# Targets:
#   make setup       - nothing to install (stdlib-only); prints the toolchain it wants
#   make setup-dev   - dev tooling for linting (best-effort: shellcheck, markdownlint)
#   make run_tests   - complete offline contract, scenario, package, CLI and site checks
#   make lint        - shellcheck + python compile check (best-effort, skips missing tools)
#   make site        - regenerate the static site into site/_site
#   make site-verify - drift gate only (committed site == skills/ on disk)
#   make clean       - remove generated site output

PY := python3
NODE ?= node
NPM ?= npm
TMPDIR ?= $(CURDIR)/.omo-tmp
THUNDERKIT_TEST_TMPDIR ?= $(TMPDIR)
PYTHONPYCACHEPREFIX ?= $(TMPDIR)/pycache
export TMPDIR THUNDERKIT_TEST_TMPDIR PYTHONPYCACHEPREFIX
export PYTHONPATH := $(CURDIR)/tests:$(CURDIR)

.PHONY: setup setup-dev run_tests lint site site-verify clean private-temp check-runtime

setup: check-runtime
	@echo "Offline development: Python 3.12.x, Node 24.x, npm 11.19.1; no project dependencies."
	@echo "Agent installation is separate: skills@1.7.0 requires Node >=22.20.0."

private-temp:
	@umask 077; mkdir -p "$(TMPDIR)" "$(THUNDERKIT_TEST_TMPDIR)" "$(PYTHONPYCACHEPREFIX)"

check-runtime: private-temp
	@command -v "$(PY)" >/dev/null || { echo "MISSING: python3 (Python 3.12.x required)"; exit 1; }
	@command -v "$(NODE)" >/dev/null || { echo "MISSING: node (Node 24.x required)"; exit 1; }
	@command -v "$(NPM)" >/dev/null || { echo "MISSING: npm (npm 11.19.1 required)"; exit 1; }
	@$(PY) -c 'import sys; sys.exit("Tests require Python 3.12.x" if sys.version_info[:2] != (3, 12) else 0)'
	@$(NODE) -e 'if (Number(process.versions.node.split(".")[0]) !== 24) { console.error("Tests require Node 24.x"); process.exit(1); }'
	@test "$$($(NPM) --version)" = "11.19.1" || { echo "Tests require npm 11.19.1"; exit 1; }

setup-dev:
	@echo "Best-effort dev tools (skips silently if absent):"
	@command -v shellcheck >/dev/null || echo "  consider: brew install shellcheck"
	@command -v markdownlint >/dev/null || echo "  consider: npm i -g markdownlint-cli"

run_tests: check-runtime
	$(PY) tests/validate_frontmatter.py
	$(PY) -m unittest discover -s tests -p 'test_*.py' -v
	$(PY) tests/skill_scenarios.py --all
	$(PY) tools/materialize_skills.py --check
# CommonJS fixture executables must not inherit this package's ESM scope.
	@set -eu; scratch=$$(mktemp -d "$(TMPDIR)/node-tests.XXXXXX"); \
	trap 'rm -rf "$$scratch"' EXIT; \
	printf '%s\n' '{"type":"commonjs"}' > "$$scratch/package.json"; \
	TMPDIR="$$scratch" $(NODE) --test tests/cli.test.mjs tests/release_*.test.mjs
	$(PY) tests/site_drift.py

# Lint is best-effort so it stays green on a fresh machine without dev tools.
lint: private-temp
	@$(PY) -m py_compile $$(find tests tools site skills -name '*.py') && echo "py_compile OK"
	$(NODE) --check bin/thunderkit.js
	@if command -v shellcheck >/dev/null; then \
		find . \( -name .git -o -name .omo -o -name .omo-tmp -o -name .omh -o -name .omc \) -prune -o -name '*.sh' -print0 | xargs -0 -r shellcheck && echo "shellcheck OK"; \
	else echo "shellcheck absent — skipped"; fi

site: private-temp
	$(PY) tools/materialize_skills.py
	$(PY) site/build.py

site-verify: private-temp
	$(PY) tests/site_drift.py

clean:
	rm -rf site/_site
