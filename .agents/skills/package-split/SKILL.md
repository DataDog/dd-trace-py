---
name: package-split
description: Inventory the APIs other modules consume, to choose package-split extraction boundaries.
---
# Package split

Run from the repository root; `--help` lists options. Both commands are static, over-approximating
inventories, not compatibility proofs, and always exit 0 on success (2 on usage or input errors).

```bash
scripts/package-split api-usage --module ddtrace.internal.coverage [--source-path ddtrace ...]
scripts/package-split api-surface [--source-path ddtrace ...] [--zones <layers.json>] [--top 30]
```

- `api-usage` lists every symbol of the target consumed outside it, with call/subclass/raise/except/read context.
  Run it over production code first, then add `--source-path tests` to see test-only usage.
- `api-surface` groups consumed symbols by owning module and zone (default:
  `scripts/import-analysis/layers.json`), and suggests modules whose consumers all live in one zone.
- JSON evidence is written to `.package-split/<command>/<command>.json` (`--output` to change).

Use the results to propose a narrow supported API for the extraction and get scope and naming
approved before moving code. Report the commands run, the proposed surface, and usages you chose
not to support. Add fixtures for new analysis cases in `tests/package_split/`, then use
`scripts/run-tests` and `scripts/lint`.
