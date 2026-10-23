# AGENTS.md — dd-trace-py

Repository instructions for coding assistants. Tool-specific entry points refer here.
Paths below are relative to the repository root.

## Development constraints

- Use the `run-tests` skill and `scripts/run-tests`; never run pytest directly.
  Discover environments with `scripts/run-tests --list <changed-files>` and use the returned hashes.
- Use the `lint` skill and `scripts/lint` for formatting and checks; never invoke raw lint tools.
  Format edited code, run relevant tests, and run `scripts/lint checks` before committing.
- Preserve public API compatibility. Never commit secrets. Remove accidental debugging output.
- Investigate source, tests, and supplied context before asking questions. Ask when unresolved
  business intent or an architectural decision would materially change the implementation.
- Do not remove TODO/NOTE comments without explicit human instruction; update them when related code changes.
- Benchmark changes to runtime hot paths, including native code. Documentation, comments, and
  test-only edits do not require runtime benchmarks. Report missing benchmark coverage.
- Update documentation when changing APIs, configuration, or contributor workflows.
  Update integration references when a shared pattern changes, rather than for every integration edit.
- Do not hide circular imports with function-local imports or type-checking guards.
  Fix ownership or dependency direction. Preserve deliberate lazy imports for startup cost or
  gevent compatibility when they are unrelated to a cycle.

## Docstrings and Comments

Use reStructuredText for Sphinx-rendered docstrings: public interfaces and modules named by
`automodule` directives in `docs/`, including integration `__init__.py` files.
Use plain prose elsewhere; avoid rST emphasis and double backticks in private docstrings and comments.
Explain non-obvious invariants, not the next line of code. Do not add `AIDEV-*` anchors;
`scripts/check_no_new_aidev_anchors.py` enforces this.

## Task-specific guidance

Use a skill when available; otherwise read its `SKILL.md` and follow its commands.
Skill files live at `.claude/skills/<name>/SKILL.md`. Read the relevant sections and references
for the task, rather than loading every guide. Source code defines current signatures and defaults.

| Task | Skill or reference |
|------|--------------------|
| Test selection, execution, environment rebuilds | `run-tests`; `docs/contributing-testing.rst` |
| Formatting, typing, quality checks | `lint`; `scripts/lint --help` |
| PR release-note decision | `releasenote`; `docs/releasenotes.rst` |
| New or renamed configuration | `add-new-configuration` |
| Contrib integration changes | `apm-integrations`; `.cursor/rules/isolated-responsibility.mdc` |
| LLMObs integration changes | `llmobs-integrations`; also `apm-integrations` when changing contrib plumbing |
| Added/moved modules or new import cycles | `circular-import-analysis` |
| Imports across foundation/product boundaries | `dependency-direction-analysis` |
| CI failures or merge blockers | `review-ci` |
| Runtime performance | `run-benchmarks` |
| Build-cache performance | `debug-build-times` |
| Native crash investigation | `analyze-crash` |
| CPython compatibility | `find-cpython-usage`, then `compare-cpython-versions` |
| AppSec / IAST | `.cursor/rules/appsec.mdc`; `.cursor/rules/iast.mdc` for IAST |
| AI Guard | `.cursor/rules/ai-guard.mdc` |
| C/C++/Rust/Cython changes | `.cursor/rules/native-code.mdc` |
| Internal APIs, products, threading, fork hooks | Relevant sections of `ddtrace/internal/README.md` |
| Benchmark CI / SLO configuration | `.gitlab/benchmarks/README.md`, `.gitlab/benchmarks/microbenchmarks.md` |
| Build or installation failures | `docs/troubleshooting.rst` |

## Pull requests

Follow the Pull Request Requirements in `docs/contributing.rst` and use
`.github/PULL_REQUEST_TEMPLATE.md`. Titles follow Conventional Commits (`commitlint.config.js`).
Include validation, risks, and relevant issues. Use `releasenote` before opening a PR;
apply `changelog/no-changelog` when no customer-facing note is needed.

This repository is public. PR text, commits, and release notes must describe the change itself.
Keep internal discussions, other teams' tickets, backend/data-pipeline names, and open internal
questions out of public text. Linking the PR's own Jira ticket is fine.

Before mirroring a fork PR into credentialed CI, review its exact head SHA and be willing to merge
it pending final checks. Follow **Merging Pull Requests from Forks** in `docs/contributing.rst`
for the shadow PR, SHA verification, and cleanup. Never mirror partially reviewed code just to run CI.

## CI scripts

Automatic retries on `main` are infrastructure-only. Jobs needing retries on other branches must
extend `.gitlab/retry-policies.yml`; do not add job-level `retry:` or duplicate jobs by branch.
Manual retries are unaffected.

For CI downloads, use `curl --fail` and transient retries (`--retry 5 --retry-delay 2
--retry-connrefused`, or an explicit loop). Download to a file before extracting/executing;
never pipe retried downloads into `tar`/`sh` or merge stderr into captured responses.
Status-aware polling and best-effort lookups may use justified alternatives with visible failure handling.
