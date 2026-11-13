# Package split source guardrails

The source-side tool keeps the work dd-trace-py owns: finding consumers and
preventing source drift during extraction. Package builds, dependencies, API
contracts and import inertness belong to the destination package's checks.

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

Evidence is written under `.package-split/api-usage/<scope>/api-usage.json` and
`.package-split/api-surface/<scope>/api-surface.json`. Scope identifiers include
source paths and, for api-usage, the target, so production, tests and different
targets do not overwrite one another. The JSON records the source paths and target.
Use `--output` to choose another evidence root.

## Freeze extracted sources

Land `package-freeze.toml` and the freeze workflow on the protected base before
copying code. The schema and example live in `freeze_manifest.py`.

```sh
scripts/package-split freeze --base <protected-revision> --head <candidate-revision>
```

Freeze manifests use schema version 2. Every approved rewrite requires a
`post_hash`, the SHA-256 of its approved replacement, and a sync record. The
protected base supplies the approval; the file at `--head` must match it. A later
edit, deletion or reversion requires a new approval on the base. Version 1 is
rejected because it cannot establish this guarantee.

The gate inspects Git objects and never executes candidate code. Its evidence
records the protected manifest hash, source revision, environment and individual
checks under `.package-split/freeze/freeze.json`. Missing required inputs fail.
Exit codes are 0 for success, 1 for a failed freeze, and 2 for usage/inventory errors.
The PR workflow skips enforcement only when its base has no freeze manifest.

## Checks owned elsewhere

| Former gate | Replacement or required extraction coverage |
| --- | --- |
| `boundaries` | Existing cycle and layering regression jobs for ddtrace; destination import and dependency policy for the extracted package. Review dynamic imports explicitly where static tooling cannot resolve them. |
| `artifacts` | Existing wheel build/install/smoke pipeline and destination package checks. Add explicit coverage for sdist parity, resources, entry points and a native-free dependency closure where promised; wheel smoke alone does not prove them. |
| `lifecycle` | The destination's import-inertness probe and package-owned activation tests, with runtime dependencies installed. Legacy ddtrace startup has its own behavior contract. |
| `contracts` | A reviewed API and consumer-owned behavior tests against exact artifacts. Signature snapshots alone miss method behavior and shared state. |
| `compatibility`, `migration` | Concrete consumer tests selected through `scripts/run-tests`: released/candidate combinations, bridge/provider identity using `is`, absent versus broken providers, single instrumentation, install order and rollback. No generic matrix runner supplies that coverage automatically. |

The source CLI no longer accepts these six commands or `--manifest`; the root
package contract and its duplicate dependency declarations are removed.
Use `scripts/package-split --help` for current options and `gates` to list the
single gate. Validate changes with `scripts/run-tests` and `scripts/lint`.

[Extraction plan](../../docs/package-split/plan.md) · [Audit and disposition](../../docs/package-split/harness-audit-2026-11.md)
