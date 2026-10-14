---
name: package-split
description: >
  Execute the dd-trace-py package split: migrate a bounded part of this repository
  (e.g. a ddtrace.internal.* module) into independently released packages under
  datadog-apm-python, with automated guardrails. Use this when asked to "run
  package-split on <module>", extract or migrate a package to the new namespaces,
  run a package-split gate (boundaries, contracts, artifacts, lifecycle,
  compatibility, migration, freeze), or analyze a module's used-API surface
  before extraction.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - Edit
  - Write
  - TodoWrite
---

# Package Split Skill

You are migrating a bounded part of dd-trace-py into independently released
packages. The full contract is **docs/package-split/plan.md**; the execution
guardrails are **docs/package-split/agent-prompt.md**; the tooling reference is
**scripts/package_split/README.md**. Read all three before doing anything else.

## Intent

Products must release independently without rebuilding unrelated native
components; shared-package releases must not require product releases; every
package builds, tests and releases from its own checkout; supported packages
coexist with one instrumentation activation; existing ddtrace behavior stays
intact throughout. Extraction is *not* moving files: a package is independent
only when release rehearsals pass.

## Destination repository and layer rule (established facts)

Destination: **https://github.com/DataDog/datadog-apm-python/** (private to
Datadog) — a uv-workspace monorepo; packages live under `packages/<name>/`
(src layout, per-package pyproject/README/riotfile/tests, shared
ruff/pyright/deptry pins at the root). It already hosts the slim seed
package `packages/ddtrace-internal` (published `ddtrace-internal==0.1.0`,
a dd-trace-py runtime dependency, provides only `_ddtrace_internal`, the
import-time module registry). Working rule of thumb for the destination
layers (final names still pending plan review):

- `ddtrace_internal_base` — all pure-Python code that depends on nothing but
  the standard library (native-free, stdlib-only: the strictest `boundaries`
  gate target);
- `ddtrace_internal_native` — the compiled code required for
  `ddtrace_internal` (native-required);
- `ddtrace_internal` — all current `ddtrace.internal` code, using the two
  above as dependencies (native stays behind optional adapters).

The graph is acyclic: `ddtrace_internal` depends on `base` and `native`;
nothing depends on `ddtrace_internal`. A pilot like
`ddtrace.internal.codeowners` (stdlib-only, 0 outgoing internal edges)
lands in `ddtrace_internal_base`. When creating packages there: register
new import roots in `.importlinter` `root_packages` (`scripts/validate_packages.py`
enforces it), respect the per-package lint/type contract, and use
`scripts/api_semver_check.py` + `release/<package>:<level>` labels for API
versioning. Seed the harness into that repository (thin
`scripts/package-split` wrapper over the same versioned harness) as part
of the first extraction's safety-net phase.

## Non-negotiable outcomes (stop and ask if you cannot meet one)

1. New package imports are **inert**: no threads, network, process creation,
   logging changes, import hooks, signal handlers or patched callables at
   import time. Activation lives in explicit lifecycle APIs. Preserve legacy
   ddtrace auto-initialization through its compatibility bootstrap only.
2. Default Test Optimization (and any declared `native-free` package) has a
   native-free runtime dependency graph. Native features are explicit,
   optional adapters that never auto-activate.
3. Imports from the new namespaces must never import `ddtrace` (the manifest's
   `forbidden_namespaces`). A bridge in ddtrace falls back only when the new
   top-level package is *genuinely absent* — a broken new installation must
   fail loudly, never silently fall back.
4. Do not break public ddtrace APIs, do not remove TODO/NOTE comments, and do
   not trade away guardrails to make extraction appear complete.
5. Never report an unrun gate as passing. Missing prerequisites and unexecuted
   required scenarios fail gates by design (`not_run` never passes).

## Do not invent missing decisions

These require human approval before you commit to them (report them as
blockers instead): final import/distribution names, package boundaries,
native-feature replacements, compatibility-window length, freeze activation.
You may always inventory and propose while decisions are pending.

## The tools

All commands run from the repository root. Every gate exits 0 on pass, 1 on
fail, 2 on usage/manifest errors, and writes JSON evidence with per-check
results under `<output>/<gate>/<gate>.json`:

```bash
# Inventory the target's used-API surface (what consumers actually use):
scripts/package-split api-usage --module <dotted.target>              # production consumers
scripts/package-split api-usage --module <dotted.target> --source-path ddtrace --source-path tests
# Repo-wide clustering: bundling candidates, shared-foundation fan-out:
scripts/package-split api-surface

# Gates (all take --manifest package-contract.toml --output <dir>; defaults shown):
scripts/package-split boundaries    # cycles, edge policy, forbidden namespaces, dynamic imports, native policy vs build metadata
scripts/package-split lifecycle    # import inertness, activation idempotency/cleanup, fork/spawn single activation
scripts/package-split artifacts    # isolated sdist+wheel build, rebuild-from-sdist parity, install outside checkout, native-free graph
scripts/package-split contracts --baseline <released wheel>   # API snapshot diff + behavioral fixtures (never auto-accepts a baseline)
scripts/package-split compatibility --matrix <file.toml>     # exact candidate/released version combinations
scripts/package-split migration --matrix <file.toml>         # old/new coexistence, broken-provider, duplicate-plugin, rollback
scripts/package-split freeze --base <target-revision>        # protected extraction provenance (see also the GitHub freeze-guard workflow)
```

Schema references: `package-contract.toml` (package contract), matrix files
(schemas in `scripts/package_split/matrices.py` docstrings), freeze manifest
(`scripts/package_split/freeze_manifest.py`). Harness versioning: gate
evidence records harness version + manifest hash + source revision.

## Workflow phases (docs/package-split/plan.md, Deliverable 4)

### Phase A — Inventory and propose (no code moves)

1. Run the target surface analyses and record the numbers:
   - `api-usage --module <target>` (production) and with `--source-path tests`
     (which tests reach into internals — those move with the package).
   - `api-surface` to see the target's consumer-zone fan-out and whether it
     is a bundling candidate or shared foundation.
2. Verify the target's import-time behavior today: `lifecycle` gate on the
   current tree; list outgoing internal dependencies (must become zero or
   explicitly approved adapters).
3. Propose: the package contract manifest for the target (supported exports =
   the *used* surface, not everything declared), allowed edges, activation API
   if any, behavioral fixtures, destination names (pending approval).
4. Report blocking decisions; obtain approval before expanding scope.

### Phase B — Safety net in place (before extraction)

- Write the destination package manifest and its gates must pass in-repo
  first: `boundaries`, `lifecycle`, `artifacts` on a candidate tree.
- Port consumer-relevant behavior tests as behavioral fixtures (they are the
  `contracts` gate's input); keep repo regression tests in this repository.
- Rehearse the negative expectations: a hidden native dep, a dynamic
  forbidden import, an import-started thread, a duplicate activation, an
  incompatible API change and a missing resource must each *fail* the right
  gate (see `tests/package_split/` for the harness's own proofs).

### Phase C — Extract incrementally (approved scope only)

- New namespaces outside `ddtrace`; no new dependency cycles; never fix
  circular imports with deferred imports (use the circular-import-analysis
  skill).
- The ddtrace-side bridge: use the new provider when installed; fall back
  only on genuinely-absent new top-level package; propagate errors from a
  broken provider; record owner + removal condition for the bridge.
- Build from a standalone checkout, test installed wheels outside the source
  tree (artifacts gate), never editable installs.
- Freeze only after boundary validation passes: create `package-freeze.toml`
  with pinned extraction revision + per-file hashes, then the
  `.github/workflows/package-split-freeze-guard.yml` PR guard enforces it
  (rules always come from the base branch; the PR head is inspected, never
  executed).

### Phase D — Verify and hand over

Run every applicable gate and record exact commands, outcomes and evidence
paths (including revisions and artifact hashes). Compatibility/migration
matrices need real wheels — craft or build them; do not skip scenarios.
Then report only: changed files and the boundary established; validation
commands, outcomes, evidence paths; unexecuted checks, blockers, residual
risks; compatibility/freeze status, rollback path, and the next approval
needed. Stop before cutover if contracts fail, dependency closure is unknown,
native code enters a native-free graph, shared state duplicates, or
source/destination parity is unproven.

## Repository rules that always apply

- Tests: never run pytest directly — use the run-tests skill
  (`scripts/run-tests`; the harness tests are the `package_split` suite).
- Lint: use the lint skill (`scripts/lint`) before every commit.
- Skills to consult: circular-import-analysis (cycles), dependency-direction
  analysis (layering), run-benchmarks (any performance-sensitive change).
- Update the contract manifest, `scripts/package_split/README.md` and this
  skill's references whenever gates or schemas change.

## Worked example: choosing a pilot target

Pilot selection criteria (apply them before proposing any target): small file
count, zero (or near-zero) outgoing internal dependencies, few concentrated
consumers across ≥2 zones (real migration scenarios), pure Python with no
native dependencies, import-inert module level, dedicated tests, off the
tracer hot path. `ddtrace.internal.codeowners` was selected exactly this way:
1 file / 199 LOC / stdlib-only / 0 outgoing edges / 1 consumed symbol
(`Codeowners`) by 3 consumers in 2 zones / dedicated tests / not hot-path.
Run `api-usage` + `api-surface` yourself to confirm any target's numbers
before proposing it.
