---
name: package-split
description: Inventory the APIs other modules consume, to choose package-split extraction boundaries.
---
# Package split

Run from the repository root; `--help` lists options. Both commands are static, over-approximating
inventories, not compatibility proofs, and always exit 0 on success (2 on usage or input errors).
Files the interpreter cannot parse are skipped and listed on stderr and in the JSON `unparsed` field.

```bash
scripts/package-split api-usage --module ddtrace.internal.coverage [--source-path ddtrace ...]
scripts/package-split api-surface [--source-path ddtrace ...] [--zones <layers.json>] [--top 30]
```

- `api-usage` lists every symbol of the target consumed outside it, with call/subclass/raise/except/read context.
  Run it over production code first, then use `--source-path ddtrace --source-path tests` for a combined inventory.
  For a test-only scan, use `--source-path tests --allow-absent-target`; re-export attribution then depends on facades present in that scope.
- `api-surface` groups consumed symbols by owning module and zone (default:
  `scripts/import-analysis/layers.json`), and suggests modules whose consumers all live in one zone.
- JSON evidence is written under `.package-split/<command>/<scope>/`; `api-usage` also nests it under the target module. The command prints the exact path (`--output` changes the root).

Use the results to propose a narrow supported API for the extraction and get scope and naming
approved before moving code. Report the commands run, the proposed surface, and usages you chose
not to support. Add fixtures for new analysis cases in `tests/package_split/`, then use
`scripts/run-tests` and `scripts/lint`.
