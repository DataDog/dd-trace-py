# Package split source inventories

The source CLI finds consumers before package extraction. Source freezes use
main's existing frozen-paths check. Package builds, dependencies, API contracts
and import inertness belong to the destination package's checks.

## Inventory consumers

Run from the repository root. Inventories default to scanning `ddtrace`; repeat
`--source-path` to change the scope. No package contract is required.

```sh
scripts/package-split api-usage --module ddtrace.internal.codeowners
scripts/package-split api-usage --module ddtrace.internal.codeowners --source-path ddtrace --source-path tests
scripts/package-split api-surface --top 15
```

`api-usage` follows import aliases and re-export chains. For a tests-only scan,
use `--source-path tests --allow-absent-target`; include production paths as well
when re-export resolution needs their definitions. An absent target otherwise
fails instead of looking unused. `api-surface` groups consumers by the existing
architectural zones in `scripts/import-analysis/layers.json`.

These are static inventories, not compatibility proofs. Unsupported syntax and
source encodings are reported as skipped files on stderr and in JSON. Review
those gaps and search non-Python configuration before approving an API boundary.
Type stubs and native extension sources participate in module discovery; Cython
sources are indexed for target matching and reported as unparsed consumers.

Evidence is written under `.package-split/api-usage/<scope>/<target>/api-usage.json` and
`.package-split/api-surface/<scope>/api-surface.json`. Scope identifiers include
source paths; api-usage also separates targets into their own directories, so
production, tests and different targets do not overwrite one another. The JSON
records the source paths and target.
Use `--output` to choose another evidence root.

## Freeze extracted sources

Use main's existing [frozen-paths.txt](https://github.com/DataDog/dd-trace-py/blob/main/frozen-paths.txt)
and [Freeze check workflow](https://github.com/DataDog/dd-trace-py/blob/main/.github/workflows/freeze-check.yml).
They are the single freeze mechanism, including for native extraction. Add
repository-relative `path=SHA-256` entries before copying code. The workflow
checks the PR merge result against both the base and merge-result lists.
Require the Freeze check status and up-to-date branches on main.

To change a frozen file, first remove its entry in a separate PR that leaves
the file unchanged. After that merges, coordinate the source change with the
extracted copy and freeze the new content again when needed. A PR cannot bypass
the freeze by changing a file and its recorded hash together.

The source CLI has no freeze command, TOML freeze manifest, or rewrite-approval
schema. Existing entries stay in frozen-paths.txt; no migration is needed.

## Checks owned elsewhere

| Former gate | Replacement or required extraction coverage |
| --- | --- |
| `boundaries` | Existing cycle and layering regression jobs for ddtrace; destination import and dependency policy for the extracted package. Review dynamic imports explicitly where static tooling cannot resolve them. |
| `artifacts` | Existing wheel build/install/smoke pipeline and destination package checks. Add explicit coverage for sdist parity, resources, entry points and restrictions on a package's own compiled artifacts; wheel smoke alone does not prove them. |
| `lifecycle` | The destination's import-inertness probe and package-owned activation tests, with runtime dependencies installed. Legacy ddtrace startup has its own behavior contract. |
| `contracts` | A reviewed API and consumer-owned behavior tests against exact artifacts. Signature snapshots alone miss method behavior and shared state. |
| `compatibility`, `migration` | Concrete consumer tests selected through `scripts/run-tests`: released/candidate combinations, bridge/provider identity using `is`, absent versus broken providers, single instrumentation, install order and rollback. No generic matrix runner supplies that coverage automatically. |

A pure-Python package must not itself build compiled artifacts. Its runtime
dependencies may include native telemetry and writers; there is no transitive
native-free requirement for Test Optimization.

The source CLI accepts only `api-usage` and `api-surface`; the generic gates,
`freeze`, `gates`, and `--manifest` are retired. The root package contract and
its duplicate dependency declarations are removed. `--source-path` selects
source scan scope; the destination CLI's `--package DIR` selects package
metadata for its own checks. These CLIs and their modules are maintained
independently rather than synchronized as copies of one harness.

Use `scripts/package-split --help` for current options. Validate changes with
`scripts/run-tests` and `scripts/lint`.

[Extraction plan](../../docs/package-split/plan.md) · [Audit and disposition](../../docs/package-split/harness-audit-2026-11.md)
