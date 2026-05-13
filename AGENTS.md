# AGENTS.md — dd-trace-py

Single source of truth for all AI coding assistants. Tool-specific entry points
(`.claude/CLAUDE.md`, `.cursor/rules/dd-trace-py.mdc`) import this file.

## Project Rules

1. **Testing** — NEVER run `pytest` directly. Use the `run-tests` skill (`scripts/run-tests`). See `docs/contributing-testing.rst`.
2. **Linting** — NEVER use raw linting tools. Use the `lint` skill (`hatch run lint:*` commands).
3. **Format and lint** — Use the `lint` skill to format files after editing and to run all checks before committing.
5. **No public API breakage** — Never change public API contracts; real applications depend on them.
6. **No secrets** — Never commit secrets; use environment variables.
7. **Don't assume business logic** — Ask when unsure about implementation details.
8. **AIDEV comments are protected** — Never remove `AIDEV-` comments without explicit human instruction. Update them when modifying related code.
9. **Test before committing** — Run relevant tests to validate changes before committing.
10. **Performance matters** — This library runs in production hot paths. Benchmark changes to C/C++/Cython/Rust code.
11. **Update docs** — Add/update documentation when changing internal or public APIs.
12. **No stray prints** — Check for and remove unexpected `print()` calls.

## Key Architecture

- **Monkey-patching** is the core instrumentation mechanism. Don't break it; understand it before modifying integrations.
- **Performance-critical code uses C/C++/Cython/Rust** — profile and benchmark when touching these paths.
- **Configuration is via environment variables** — follow existing patterns in `ddtrace/internal/settings/`.
- **Integrations are modular** — each lives under `ddtrace/contrib/` and follows the `Pin`/`patch`/`unpatch` pattern.

## AIDEV Anchor Comments

Add `AIDEV-NOTE:`, `AIDEV-TODO:`, or `AIDEV-QUESTION:` comments as inline knowledge for AI and developers.

- Before scanning files, **grep for existing `AIDEV-*` anchors** in relevant subdirectories first.
- **Update relevant anchors** when modifying associated code.
- **Never remove** `AIDEV-NOTE`s without explicit human instruction.
- Add anchors when code is complex, important, confusing, or potentially buggy.

## PR Guidelines

Follow **`docs/contributing.rst`** ("Pull Request Requirements" and "Branches and Pull Requests" sections).

- Use `.github/PULL_REQUEST_TEMPLATE.md` for PR descriptions.
- **PR titles must follow Conventional Commits** (`commitlint.config.js`): `type(scope): description`. Common types: `feat`, `fix`, `chore`, `refactor`, `docs`, `test`, `perf`, `ci`. Scope is optional. Example: `fix(tracing): resolve span link propagation issue`.
- Link relevant issues or JIRA tickets; include a testing plan.
- When reviewing/generating PRs, check for: missing sections, missing changelog, missing tests, backward-compatibility risks.
- **Release notes are required** before opening a PR. Use the `releasenote` skill to generate one (see `docs/releasenotes.rst` for style guidelines). If the change is not user-impacting (e.g., CI chores, internal refactors, test-only changes), add the `changelog/no-changelog` label to the PR instead.

## Skills

Use the Skill tool to invoke these. **Always prefer skills over raw commands.**

| Skill | Trigger |
|-------|---------|
| `run-tests` | Running any tests or validating code changes. **Never run pytest directly.** |
| `lint` | Formatting, style/type/security checks, or before committing. **Never skip before commits.** |
| `releasenote` | Creating or updating release notes for the current branch. |
| `find-cpython-usage` | Investigating CPython API dependencies or adding a new Python version. |
| `compare-cpython-versions` | Comparing CPython source between two Python versions. |
| `circular-import-analysis` | Detecting circular imports and proposing architectural fixes. Use when the CI job reports new cycles, or proactively when adding/moving modules. |
| `review-ci` | Reviewing CI results for a branch/commit/PR. Use when CI is failing or to understand what's blocking a PR from merging. Requires Datadog MCP. |
| `run-benchmarks` | Running performance benchmarks to measure the impact of code changes. Use when touching performance-sensitive code or asked about perf impact. |
| `debug-build-times` | Diagnosing slow base venv builds or warm rebuild regressions. Use when ext_cache isn't saving time or when CI venv builds are unexpectedly slow. |

## Domain Guides

**Read the corresponding guide before modifying code in these domains:**

| Domain | Guide | Paths |
|--------|-------|-------|
| Application Security (AppSec) | `.cursor/rules/appsec.mdc` | `ddtrace/appsec/`, `tests/appsec/` |
| IAST | `.cursor/rules/iast.mdc` | `ddtrace/appsec/_iast/`, `tests/appsec/iast*/` |
| Native Code (C/C++/Rust/Cython) | `.cursor/rules/native-code.mdc` | `*.c`, `*.cc`, `*.cpp`, `*.h`, `*.hh`, `*.hpp`, `*.rs`, `*.pyx`, `*.pxd` |
| Repository Structure | `.cursor/rules/repo-structure.mdc` | — |
| Linting | `.cursor/rules/linting.mdc` | — |
| Testing | `.cursor/rules/testing.mdc` | — |
