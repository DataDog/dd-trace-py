# package-split harness

Versioned validation gates for the dd-trace-py package split, implementing
docs/package-split/plan.md Deliverable 2. One copy of this directory lives in
each participating repository (dd-trace-py now, datadog-apm-python later);
`scripts/package-split` is the only public interface.

## Usage

```
scripts/package-split <gate> [--manifest package-contract.toml] [--output <dir>] [gate options]
```

Exit codes: `0` gate passed, `1` gate failed (including missing
prerequisites), `2` usage/manifest errors. Every gate writes a JSON
evidence file (`<output>/<gate>/<gate>.json`) recording the source revision,
manifest hash, harness version, environment and individual pass/fail/skip
results for each check. `not_run` (missing prerequisite or unexecuted
required scenario) never counts as passing; `skip` is visible but neutral.

## Gates

| Gate | Purpose |
| --- | --- |
| `boundaries` | Import cycles, manifest edge policy (including parent-initializer expansion and forbidden namespaces), literal and non-literal dynamic imports, build/runtime metadata vs the declared contract (hidden native dependencies). |
| `contracts --baseline <wheel>` | Supported exports, signatures, defaults, annotations and exception bases compared against an immutable released wheel; consumer behavioral fixtures run against the candidate. Purely comparative: no baseline is ever written or auto-accepted. |
| `artifacts` | Isolated PEP 517 build of sdist + wheel, wheel rebuild from the sdist, install into a fresh venv outside the checkout (no editable/source leakage), metadata, entry points, declared resources; native-free policy enforced on the wheel and the whole resolved runtime graph. |
| `lifecycle` | Fresh-subprocess probe: import targets must be inert (no threads, network, process creation, logging changes, import hooks, signal handlers, atexit callbacks, patched callables); activation must be idempotent, clean up after deactivation and activate exactly once per process under fork and spawn. |
| `compatibility --matrix <file>` | Scenario matrix installing exact candidate/released combinations into fresh venvs; unsupported combinations must be rejected clearly (`imports_fail`); required scenarios that cannot execute fail the gate. |
| `migration --matrix <file>` | Old-only/new-only/coexistence/upgrade/removal/rollback scenarios; file ownership (no overlapping RECORDs), canonical bridge identity (broken new providers must fail loudly, not silently fall back), exactly one effective plugin per entry-point group. |
| `freeze --base <rev>` | Protected `package-freeze.toml` read from the base revision (a PR cannot edit its own rules); hash-pinned extraction provenance; unauthorized edits/additions/deletes/renames rejected; approved rewrites and expiring exceptions require owner, reason and sync record. |

`scripts/package-split gates` lists the available gates.

## api-usage / api-surface (informational, not gates)

**api-usage** (`scripts/package-split api-usage --module ddtrace.internal.coverage [--source-path ...]`) computes
the **used-API surface** of one target module: which of its symbols other
modules actually consume (via alias tracking, from-imports, attribute chains,
relative and star imports), classified by how they are used (call /
subclassed / exception-caught / raised / read), with importer and location
evidence in `<output>/api-usage/api-usage.json`.

**api-surface** (`scripts/package-split api-surface [--zones scripts/import-analysis/layers.json] [--top N]`)
runs the same detection once over the whole tree and attributes every use to
its owning module, then clusters consumers by zone (a layers.json-style zone
config, reusing the repository's own architectural vocabulary when present):

- symbols whose consumers all sit in one other zone are **bundling
  candidates** (move them -- or their whole owner module -- with that
  zone's package instead of the shared foundation);
- symbols consumed across several zones are genuine shared surface that
  must survive extraction;
- symbols with no consumers outside their own package can stay private.

Consumers inside the owner's own package are excluded from blocking (they
move with the package when it is extracted). Results land in
`<output>/api-surface/api-surface.json`, including a full per-owner symbol
matrix.

These are the complements of `[contracts].supported_exports`: the manifest
records what a package promises, api-usage/api-surface compute what the rest
of the repository de-facto depends on -- the input for deciding stable APIs
and package boundaries before an extraction. Run api-usage once over
production paths only (the surface that must survive extraction) and once
including tests (which additionally shows which tests reach into internals
and should move with the package).

Known over-approximations, deliberate for API pinning: chains are recorded
as deep dotted paths (`Class.attr`), re-exports are only seen when the
symbol is imported from the target itself, and dynamic attribute access
(`getattr`) is not resolved.

## Manifests

- `package-contract.toml` (repo root) -- the machine-readable package
  contract (Deliverable 1). Strictly validated against a versioned schema;
  unknown fields, wrong types and unknown schema versions are rejected. The
  current dd-trace-py manifest is PROVISIONAL and records today's
  single-distribution reality; it tightens as extraction proceeds.
- `package-freeze.toml` (repo root, created at phase 4) -- extraction
  provenance: pinned revision, per-file hashes, destination, owner,
  approved rewrites and exceptions with expiry. Schema in
  `freeze_manifest.py`.

Matrix file schemas (compatibility and migration) are documented in
`matrices.py` docstrings.

## CI wiring

- Tests live in `tests/package_split/` and run through the repo's normal
  `scripts/run-tests` flow via the `package_split` suite in
  `tests/suitespec.yml` (Python 3.9-3.14). They include the plan's negative
  fixtures: every gate must *fail* on a hidden native dependency, a dynamic
  forbidden import, an import-started thread, a broken new provider,
  duplicate activation, an incompatible API change, a missing package
  resource and an unauthorized freeze edit.
- `.github/workflows/package-split-freeze-guard.yml` enforces the freeze on
  every PR: it runs the *base branch's* harness and freeze rules against the
  PR head (never executing PR code), so freezing is open to anyone while
  unfreezing requires a landed change on the protected branch.

## Layout

- `cli.py` -- argument parsing, dispatch, evidence writing, exit codes.
- `evidence.py` -- check/report model and the fail-closed semantics.
- `manifest.py` / `freeze_manifest.py` / `matrices.py` -- versioned schemas.
- `astdeps.py` -- stdlib-only static import analysis (module graph, parent
  initializer expansion, guarded/dynamic imports, cycles). Complements
  scripts/import-analysis (which stays the owner of repo-internal layer
  policy); it deliberately does not depend on it so the harness works from
  any checkout.
- `probe.py` -- fresh-subprocess probe used by the lifecycle, contracts,
  compatibility and migration gates.
- `venvutil.py`, `wheelhouse.py` -- venv creation/installation (stdlib venv
  + pip, isolated from the creating environment) and wheel/sdist
  inspection.
- `gates/` -- one module per gate.

## Extending

Add a gate by writing `gates/<name>.py` with `run_gate(manifest, args,
report)` and registering it in `gates/__init__.py`'s REGISTRY, then adding
its CLI arguments in `cli.py`. Negative fixtures for new checks go in
`tests/package_split/` and must prove the check fails closed.
