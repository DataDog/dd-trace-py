# Package split: plan and validation gates

Status: proposal. Package names and the tooling interfaces below are provisional.
The namespace extraction POC has not yet been reviewed for this plan.
See [the agent prompt](agent-prompt.md) for execution guardrails.

The validation harness described in Deliverable 2 now exists:
`scripts/package-split` (thin wrapper) over `scripts/package_split/`
(versioned harness), the provisional `package-contract.toml` at the repo
root, its negative-fixture test suite under `tests/package_split/` and the
PR-level freeze guard at `.github/workflows/package-split-freeze-guard.yml`.
See `scripts/package_split/README.md` for usage.

## Required outcomes

- Products release independently, without rebuilding unrelated native components.
- Compatible shared-package releases require no product release.
- Every package builds, tests and releases from its own checkout using declared dependencies.
- Supported packages coexist without duplicate instrumentation or conflicting shared state.
- Existing ddtrace users retain supported behavior throughout the migration. Separately approved
  removals, including the legacy pytest v2 plugin and IAST pytest integration, remain separate work.
- The shared pure-Python package has broad contents but a deliberately narrow supported API.
- The default Test Optimization installation and its transitive runtime dependencies are native-free
  (excluding the interpreter/standard library). Features requiring native components must be explicit,
  optional adapters, absent from the default dependency graph and never activated automatically.

## Architecture

Destination repository: **https://github.com/DataDog/datadog-apm-python/** (private to
Datadog; a uv-workspace monorepo). Packages live as workspace members under
`packages/<name>/` — src layout, per-package pyproject/README/riotfile/tests, with shared
ruff/pyright/deptry pins at the root. It already hosts a very slim seed package:
`packages/ddtrace-internal`, whose published `ddtrace-internal==0.1.0` wheel (a dd-trace-py
runtime dependency today) provides only `_ddtrace_internal` (import-time module registry).
The split grows this into the full destination layout. Destination-side rules that layer
packages must integrate with: new import roots must be registered in `.importlinter`
`root_packages` (enforced by `scripts/validate_packages.py`, which also pins the shared
lint/type contract), and public API versioning runs through `scripts/api_semver_check.py`
and the auto-managed `release/<package>:<level>` PR labels. The package-split harness and
this plan should be seeded into that repository (a thin `scripts/package-split` wrapper over
the same versioned harness) as part of the first extraction's safety-net phase.

Agreed working rule of thumb for the destination layers (final naming still
subject to the plan's review gates):

- `ddtrace_internal_base` — all pure-Python code that depends on nothing but the
  standard library.
- `ddtrace_internal_native` — the compiled code required for `ddtrace_internal`.
- `ddtrace_internal` — all current `ddtrace.internal` code, using the two above
  as dependencies.

This maps onto the intended layers below (`base` = Shared Python, `native` =
Shared native, `ddtrace_internal` = the migrated `ddtrace.internal` content),
gives the `boundaries` gate a concrete native-policy target (`base` is
native-free and stdlib-only; `native` is native-required; `ddtrace_internal`
keeps native behind optional adapters), and keeps the graph acyclic:
`ddtrace_internal` depends on `base` and `native`; nothing depends on
`ddtrace_internal`.

Use distinct import namespaces outside ddtrace, as in the current POC. Separate distribution names
from import names; finalize neither here. Intended layers:

- **Shared Python:** reusable pure-Python primitives; no dependency on native or product packages.
- **Shared native:** minimal compiled primitives, independently built and published.
- **Native-dependent services:** meaningful capabilities built on native primitives, not part of
  the pure-Python foundation.
- **Coverage:** a separate capability package with its own contract.
- **Products:** Test Optimization first; other products follow the same gates.

Enforce an acyclic dependency graph. Classify transitive runtime and build dependencies, not file
extensions alone. Existing Test Optimization native dependencies must be removed or isolated;
choosing replacements requires behavioral and performance evidence.

**Imports must be side-effect free with respect to application behavior:** defining objects and
normal import bookkeeping are fine; starting threads, making network calls, installing hooks,
patching functions, changing application logging or activating products are not. Activation belongs
in explicit lifecycle APIs. Preserve existing ddtrace auto-initialization through its compatibility
bootstrap, not through imports of the new shared packages.

## Deliverable 1: a package contract manifest

Proposed location in each repository: `package-contract.toml`. Validate it against a versioned schema.
Record:

| Area | Required fields |
| --- | --- |
| Identity | Distribution, import roots, owner, source paths, destination repository |
| Dependencies | Allowed package edges, runtime/build dependencies, extras, native policy |
| Compatibility | Supported Python/platforms, dependency ranges, deprecation policy |
| Contracts | Supported exports, behavioral fixtures, activation API, shared-state ownership |
| Validation | Consumer contract suites, supported version combinations, feature/performance budgets |
| Release | Build/test commands, artifact expectations, publishing owner, rollback procedure |

Keep public customer APIs, supported package-to-package APIs and private implementation distinct.
Signatures are only part of the contract: defaults, exceptions, configuration precedence, event
schemas, serialization and lifecycle behavior also need tests.

## Deliverable 2: executable gates

**Implemented:** the versioned harness lives at `scripts/package_split/` with the
thin `scripts/package-split` wrapper; see `scripts/package_split/README.md`.
Remaining proposals below are provisional. It must work without sibling checkouts.
Reuse `scripts/import-analysis/` for dependency analysis and the repository's test/lint wrappers;
do not create a second test runner. Every gate emits a readable summary and JSON evidence containing
source revisions, artifact hashes, resolved versions and individual pass/fail/skip results.
Missing prerequisites or unexecuted required scenarios must never count as passing.

Proposed interface: `scripts/package-split <gate> --manifest package-contract.toml --output <directory>`.
Additional inputs are listed below.

| Gate | Implementation and pass criteria |
| --- | --- |
| `boundaries` | Extend static import/layer/cycle checks to distribution ownership, including parent initializers. Inspect build/runtime metadata and exercise dynamic imports with forbidden namespaces blocked. Reject undeclared edges and cycles. |
| `contracts --baseline <released-artifact>` | Compare supported exports, signatures and types against immutable released artifacts; run consumer-owned behavioral fixtures against the candidate. Compatible releases must pass both checks. No automatic baseline acceptance. |
| `artifacts` | Build wheel and source distribution in an isolated environment; rebuild the wheel from the source distribution. Install outside the checkout, with no editable installs or source-path leakage; check metadata, resources, entry points and dependency consistency. Native-free builds run without compiler toolchains and reject native artifacts in the resolved runtime graph. |
| `lifecycle` | In fresh subprocesses, snapshot threads, logging, import hooks, patched callables and registered handlers; intercept network/process creation during import. Check no application side effects. Explicit activation/deactivation tests check idempotency, resource cleanup, import order and supported fork/spawn modes. |
| `compatibility --matrix <file>` | Install exact candidate/released wheel combinations: minimum supported dependencies, latest compatible versions, new providers with supported older consumers, and unsupported combinations. Run contracts and central end-to-end tests; reject unsupported combinations clearly. |
| `migration --matrix <file>` | Exercise old-only, new-only and coexistence environments, supported upgrade/install orders, removal of either product and rollback. Check file ownership, canonical shared objects and exactly one effective plugin/collector/writer. |
| `freeze --base <target-revision>` | Compare changes against the protected freeze manifest described below. Reject unauthorized edits, additions, deletions, renames and changes to extraction inputs. |

Build negative fixtures for the harness itself: a hidden native dependency, dynamic forbidden import,
import-started thread, broken new provider, duplicate activation, incompatible API change, missing
package resource and unauthorized freeze edit must each fail the appropriate gate.

### Why installation tests matter with different namespaces

Different namespaces prevent many file collisions, but not duplicate plugins, registries or behavior.
A temporary old/new import bridge must:

- Use the new provider whenever it is installed; avoid importing ddtrace in that path.
- Fall back only when the new top-level package is genuinely absent. A broad `except ImportError`
  can hide a broken provider or a missing dependency. Missing symbols/submodules must fail, not fallback.
- Preserve canonical class/exception/registry identity when both import paths are supported.
- Have an owner and removal condition. Do not promise arbitrary historical version combinations.

Test those cases explicitly, including a deliberately broken new installation. Uninstall/rollback
checks verify that compatibility shims and entry points still refer to an installed implementation.

## Deliverable 3: CI and ownership

| Where | Required checks |
| --- | --- |
| Each package PR | Boundaries, contracts, isolated artifacts, lifecycle, freeze and focused feature tests |
| Candidate release | Supported dependency-version matrix; product-specific correctness/performance tests |
| dd-trace-py | Central end-to-end coexistence and migration matrix using candidate plus released artifacts |
| Scheduled | Wider Python/platform/dependency matrix and independent-build/release rehearsals |

Package-local contracts must run independently. Central end-to-end CI consumes candidate artifacts
without requiring a ddtrace release or rebuilding unrelated packages. Keep candidate jobs isolated
from publishing credentials; a release records the exact artifact hashes tested centrally.

Every extracted module supplies feature-specific fixtures under this common framework. For coverage,
these include interpreter-specific instrumentation, worker merging, import-time coverage, test-selection
correctness and overhead. Future modules define equivalent correctness and performance budgets.
For Test Optimization, test the surviving plugin's discovery, options, xdist behavior, exit codes and
single activation; do not rebuild compatibility with integrations already approved for removal.

## Deliverable 4: controlled extraction and release

1. **Review the POC:** obtain its revision, inventory dependencies/side effects and write manifests.
   Confirm which optional Test Optimization features need adapters. Record unresolved decisions.
2. **Establish boundaries in place:** implement the harness and negative fixtures, remove forbidden
   coupling and introduce explicit activation. Keep legacy customer behavior covered by regression tests.
3. **Prove extraction:** produce candidate wheels under new namespaces and pass artifact, contract,
   lifecycle, version-skew and migration gates before freezing.
4. **Freeze and transfer:** maintain a protected `package-freeze.toml` containing source paths,
   pinned extraction revision/hashes, destination, owner, relevant build/generated inputs and approved
   rewrites. Include associated contract tests. Validate using the target branch's manifest so a PR
   cannot bypass the gate by editing its own rules. Exceptions need owner approval, reason, expiry and
   a synchronization/backport record. Verify destination parity, allowing only approved rewrites.
5. **Release rehearsal and cutover:** build/test from a standalone checkout; verify names, licenses,
   ownership, trusted publishing, security response and rollback. Demonstrate a compatible shared-package
   release against existing consumers without changing their versions. Publish only with approval.
6. **Retire bridges:** after the approved compatibility window, remove temporary fallbacks and frozen
   duplicates in a separately reviewed change; keep the gates for subsequent extractions.

Freeze prevents source drift, not dependency-induced behavior changes: contract tests stay active.
A package is independent only when these release rehearsals pass, not when its files have moved.
