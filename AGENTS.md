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
- Benchmark changes to native runtime code and other performance-sensitive paths, including
  startup. Documentation, comments, and test-only edits do not require runtime benchmarks.
  Report missing benchmark coverage.
- Update documentation when changing APIs, configuration, or contributor workflows.
  When modifying integrations, review the relevant integration skills and update references that
  describe changed patterns.
- Do not hide circular imports with function-local imports or type-checking guards.
  Fix ownership or dependency direction. Preserve deliberate lazy imports for startup cost or
  gevent compatibility when they are unrelated to a cycle.

## Docstrings and Comments

Choose the style by **whether Sphinx renders the docstring**, not whether the symbol or module
is private. Check directives and member options in `docs/` when unsure; an underscore alone does
not establish that a docstring is unrendered. Integration `__init__.py` docstrings under
`ddtrace/contrib/internal/` are rendered through `docs/integrations.rst` despite their private path.

Rendered docstrings use reStructuredText, including parameter and exception fields when useful:

```python
def enable(self, flush_interval: float = 1.0) -> None:
    """Start the writer, flushing every ``flush_interval`` seconds.

    :param flush_interval: Seconds between flushes.
    :raises RuntimeError: If the writer is already running.
    """
```

Unrendered helpers (for example, `_flush()` when not included in the docs) and inline comments
use plain prose: write parameter names as-is, without rST emphasis or double backticks.
A short summary is usually enough; field lists are optional for unrendered docstrings.
Explain non-obvious invariants, not the next line of code. Do not add `AIDEV-*` anchors;
`scripts/check_no_new_aidev_anchors.py` enforces this.

`tests/` and `scripts/` are not Sphinx-rendered, so a double-backtick literal added there is
noise. `scripts/check_no_rst_markup_in_prose.py` blocks new ones on added diff lines only;
pre-existing occurrences stay. The checker and its test are excluded because they document
the pattern. Write identifiers as plain text, for example `finish()`.

## Task-specific guidance

Repository landmarks: tracing implementation is in `ddtrace/_trace/`, public tracing exports
in `ddtrace/trace/`, and startup in `ddtrace/bootstrap/`. Contrib patch modules live in
`ddtrace/contrib/internal/`; LLMObs extraction classes live in `ddtrace/llmobs/_integrations/`.

Shared event dispatch is in `ddtrace/internal/core/`; tracing subscribers are in
`ddtrace/_trace/subscribers/`. Configuration lives in `ddtrace/internal/settings/`, Rust in
`src/native/`, and profiling native code under `ddtrace/internal/datadog/profiling/`.

Use a skill when available; otherwise read its `SKILL.md` and follow its commands.
Shared skills live at `.agents/skills/<name>/SKILL.md` and are discovered directly by Codex,
Cursor, OpenCode, and Pi. `.claude/skills` is a compatibility symlink to `.agents/skills`.
Add and edit skills in `.agents/skills` so every harness uses the same files. Read the relevant
sections and references for the task, rather than loading every guide. Source code defines
current signatures and defaults.

| Task | Skill or reference |
|------|--------------------|
| Test selection, execution, environment rebuilds | `run-tests`; `docs/contributing-testing.rst` |
| Formatting, typing, quality checks | `lint`; `scripts/lint --help` |
| PR release-note decision | `releasenote`; `docs/releasenotes.rst` |
| New or renamed configuration | `add-new-configuration` |
| Contrib integration changes | `apm-integrations`; `.cursor/rules/isolated-responsibility.mdc` |
| LLMObs integration changes | `llmobs-integrations`; also `apm-integrations` when changing contrib plumbing |
| Added/moved modules or new import cycles | `circular-import-analysis` |
| Architectural dependency direction | `dependency-direction-analysis` |
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
Include validation, risks, and relevant issues. Before deciding or answering whether a release
note is needed, invoke `releasenote` (or read `.agents/skills/releasenote/SKILL.md` if skill
invocation is unavailable), including for internal-only refactors. Follow its decision workflow;
when no note is needed, explain why and apply `changelog/no-changelog` to the PR.

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

For `curl` calls in CI, use `--fail` and transient retries (`--retry 5 --retry-delay 2
--retry-connrefused`, or an explicit loop). Download to a file before extracting/executing;
never pipe retried downloads into `tar`/`sh` or merge stderr into captured responses.
Status-aware polling and best-effort lookups may use justified alternatives with visible failure handling.
