# thunderkit — opinionated multi-model delegation for very large repos
#
# Targets:
#   make setup       - nothing to install (stdlib-only); prints the toolchain it wants
#   make setup-dev   - dev tooling for linting (best-effort: shellcheck, markdownlint)
#   make run_tests   - frontmatter validator + roster/leakage checks + site drift gate
#   make lint        - shellcheck + python compile check (best-effort, skips missing tools)
#   make site        - regenerate the static site into site/_site
#   make site-verify - drift gate only (committed site == skills/ on disk)
#   make clean       - remove generated site output

PY := python3

.PHONY: setup setup-dev run_tests lint site site-verify clean

setup:
	@echo "thunderkit needs only python3 (stdlib) to build + test."
	@command -v $(PY) >/dev/null && echo "  python3: $$($(PY) --version)" || { echo "  MISSING: python3"; exit 1; }
	@echo "For install into agents: the vercel 'skills' CLI (npx skills)."

setup-dev:
	@echo "Best-effort dev tools (skips silently if absent):"
	@command -v shellcheck >/dev/null || echo "  consider: brew install shellcheck"
	@command -v markdownlint >/dev/null || echo "  consider: npm i -g markdownlint-cli"

run_tests:
	$(PY) tests/validate_frontmatter.py
	$(PY) tests/site_drift.py

# Lint is best-effort so it stays green on a fresh machine without dev tools.
lint:
	@$(PY) -m py_compile tests/validate_frontmatter.py tests/site_drift.py site/build.py && echo "py_compile OK"
	@if command -v shellcheck >/dev/null; then \
		find . -name '*.sh' -not -path './.git/*' -print0 | xargs -0 -r shellcheck && echo "shellcheck OK"; \
	else echo "shellcheck absent — skipped"; fi

site:
	$(PY) site/build.py

site-verify:
	$(PY) tests/site_drift.py

clean:
	rm -rf site/_site
