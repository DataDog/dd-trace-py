# AGENTS.md — dd-trace-py

Single source of truth for all AI coding assistants. Tool-specific entry points
(`.claude/CLAUDE.md`, `.cursor/rules/dd-trace-py.mdc`) import this file.

## Project Rules

1. **Testing** — NEVER run `pytest` directly. Use the `run-tests` skill (`scripts/run-tests`). See `docs/contributing-testing.rst`.
2. **Linting** — NEVER use raw linting tools. Use the `lint` skill (`scripts/lint <subcommand>`).
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

### Tracing pipeline

`Tracer.trace()` / `start_span()` → `Span` (`ddtrace/_trace/span.py`) → on finish, `SpanAggregator` collects spans into traces → runs the `TraceProcessor` chain → dispatches to a `TraceWriter`.

Writer implementations (all in `ddtrace/internal/writer/writer.py`):
- `NativeWriter` — default, sends to the local Datadog Agent
- `AgentlessTraceWriter` — used when `_DD_APM_TRACING_AGENTLESS_ENABLED=1`; intake URL is resolved via `AgentlessTraceWriter.compute_intake_url(site)` which looks up `AgentlessTraceWriter.INTAKE_URLS` then falls back to a `browser-intake-{site}` derivation algorithm
- `LogWriter` — serverless environments; writes JSON to stdout

### Event bus and product decoupling

`ddtrace/internal/core.py` is the event bus. Integrations in `contrib/` emit named events (`core.dispatch()`); products (AppSec, LLMObs, CI Visibility) register handlers (`core.on()`). **Products never import directly from `contrib/`**. See `.cursor/rules/isolated-responsibility.mdc` for the full contract.

### Configuration

`ddtrace/internal/settings/_config.py` holds the global `config` object. `ddtrace/internal/settings/env.py` provides env-var reading helpers. Product-specific settings live in sibling modules (`settings/asm.py`, `settings/profiling.py`, etc.). All public knobs map to `DD_*` env vars.

### LLMObs (`ddtrace/llmobs/`)

`_llmobs.py` (~3 000 lines) is the monolithic core — span lifecycle, sampling, context propagation, and distributed tracing all live here.

Key patterns:
- **Sampling** — decided once at the root span (`span.parent_id is None`). Non-root spans inherit the parent's decision. If no upstream decision is propagated, defaults to `sampling_decision="1"`, `sample_rate="1"`. Propagated decisions are used as-is.
- **Wire format** — sampling propagates via two span meta keys: `_dd.p.llmobs_sr` (rate) and `_dd.p.llmobs_sd` (decision).
- **Evaluators** (`_evaluators/`) — separate subsystem for running automated evaluations; uses `EvaluatorRunnerSampler` which always requires a `Span` object.
- **Writer** (`_writer.py`) — buffers LLMObs events and flushes to the LLMObs intake endpoint independently of the APM writer.

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
| AI Guard | `.cursor/rules/ai-guard.mdc` | `ddtrace/appsec/ai_guard/`, `ddtrace/appsec/_ai_guard/`, `tests/appsec/ai_guard/` |
| Isolated Responsibility (security vs. shared integrations) | `.cursor/rules/isolated-responsibility.mdc` | `ddtrace/contrib/`, `ddtrace/appsec/` |
| Native Code (C/C++/Rust/Cython) | `.cursor/rules/native-code.mdc` | `*.c`, `*.cc`, `*.cpp`, `*.h`, `*.hh`, `*.hpp`, `*.rs`, `*.pyx`, `*.pxd` |
| Repository Structure | `.cursor/rules/repo-structure.mdc` | — |
| Linting | `.cursor/rules/linting.mdc` | — |
| Testing | `.cursor/rules/testing.mdc` | — |
