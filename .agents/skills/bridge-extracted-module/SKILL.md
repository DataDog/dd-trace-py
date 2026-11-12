---
name: bridge-extracted-module
description: >
  Bridge a ddtrace module that has been extracted to an external package in
  datadog-apm-python: generate the bridge PR scaffold, keep every coordinated
  piece in place, and verify before the package is published. Use when a module
  moves out of dd-trace-py into a _ddtrace_internal* distribution.
allowed-tools:
  - Bash
  - Read
  - Edit
  - Write
  - Grep
  - Glob
---

# Bridge an extracted module

A bridge is a ddtrace module whose entire implementation is imports from an
extracted ``_ddtrace_internal*`` distribution (first example:
``ddtrace/internal/codeowners.py`` -> ``_ddtrace_internal_base.codeowners``).
The bridge pattern has one hard rule and several coordinated pieces; forgetting
a piece is what this skill and the automated checks prevent.

**Hard rule**: no fallback. The bridge fails loudly if the extracted package is
missing. The vendored code stays in git history for reverts.

## Workflow

1. **Generate the scaffold** (deterministic edits only):

   ```bash
   scripts/package-split make-bridge \
     --module ddtrace/internal/codeowners.py \
     --distribution ddtrace-internal-base \
     --version 0.1.0
   ```

   This writes the bridge module (star re-export plus explicit ``x as x``
   re-exports -- the repo's ``no_implicit_reexport = true`` mypy config requires
   the alias form), adds the runtime dependency and the lint-group pin to
   ``pyproject.toml``, regenerates both ``requirements.csv`` files, writes the
   bridge resolution test, and scaffolds the reno note.

2. **Run the gate** -- it verifies every coordinated piece:

   ```bash
   scripts/lint bridged-modules
   ```

   Checks: runtime dependency present; exact pin in the ``lint`` dependency
   group (mypy runs against the lint group only -- a missing pin silently
   degrades every bridged name to ``Any``); both requirements CSVs in sync; a
   test imports both the bridge and the extracted module; every re-exported
   name exists on the extracted module; every name in-repo consumers import
   from the bridge exists on the extracted module.

3. **Judgment calls that stay with you** (the generator cannot decide these):
   - Test suite selection via the `run-tests` skill: the module's own suite,
     its consumers' suites, and a startup smoke suite (``detect_global_locks``).
   - Consumer audit: ``rg "from ddtrace.internal.<module> import"`` -- anything
     consumers import must survive the extraction (the gate enforces the
     automated part).
   - Release note: the generator scaffolds an ``upgrade`` note (a new
     runtime dependency is customer-impacting: private mirrors must mirror it).
     Confirm or adjust via the `releasenote` skill.

4. **PR mechanics**: draft until the package is published. CI environment builds
   resolve dependencies strictly from PyPI, so the PR cannot go green before
   publication -- do not add vendored fallbacks or workspace overrides to work
   around it. Update ``docs/package-split/bridges.md`` with the new row.

## Verifying before the package is on PyPI

Local verification needs the extracted package importable without PyPI. Build
the wheel from the datadog-apm-python branch and expose it via a local PEP 503
index:

```bash
cd <datadog-apm-python>
git archive <branch> packages/ddtrace-internal-base | tar -x -C /tmp/pkg --strip-components=2
(cd /tmp/pkg && uv build --wheel -o <main-repo>/.cache/local-simple/wheels)
# <main-repo>/.cache/local-simple/ddtrace-internal-base/index.html links to
# ../wheels/<wheel> (sha256-anchored); the main repo's .cache is what the
# testrunner container mounts at /home/bits/.cache.
```

- For ``scripts/run-tests``: temporarily add
  ``UV_INDEX: "file:///home/bits/.cache/local-simple"`` to the ``testrunner``
  service environment in ``docker-compose.base.yml`` (do not commit).
- For ``scripts/lint`` on the host:
  ``export UV_INDEX=file:///<main-repo>/.cache/local-simple``.
- Remove both once the package is published.

The wheel must be rebuilt when the extracted package changes, and it should
match what will be published.

## Enforcement map

| Invariant | Enforced by |
|---|---|
| Bridge is imports-only from extracted packages | gate bridge detection |
| Runtime dependency declared | ``scripts/lint bridged-modules`` |
| Lint-group exact pin (mypy visibility) | ``scripts/lint bridged-modules`` |
| requirements.csv files in sync | gate + CI ``check_requirements_lockfiles`` |
| Identity/coverage test exists | ``scripts/lint bridged-modules`` |
| Re-exports and consumer imports survive | ``scripts/lint bridged-modules`` (dynamic import) |
| mypy/ruff/security/bandit | ``scripts/lint checks`` |
| Release note present | reno + `releasenote` skill |
| Package published before merge | draft PR + CI resolution (cannot be checked locally) |

The gate runs in ``scripts/lint checks`` (CI) and as pre-commit hook
``13-check-bridged-modules`` (staged index).
