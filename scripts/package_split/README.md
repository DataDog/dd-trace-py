# Package-split harness (freeze-first)

The source side of the package split: once a module is scheduled for
extraction to datadog-apm-python, its dd-trace-py location is **frozen on
main before anything moves**. From that point, changes to frozen paths land
only as approved rewrites that pin their exact content.

```
./scripts/package-split freeze --base <revision> [--head <revision>] [--freeze package-freeze.toml]
```

- Exit codes: 0 = pass, 1 = fail (a required check reporting `not_run` also
  fails), 2 = usage or manifest/contract error.
- Evidence: `<output>/freeze/freeze.json` — source revision, manifest hash,
  environment, checks with detail.
- This subset registers only the `freeze` gate. The remaining gates
  (boundaries, lifecycle, artifacts, contracts, compatibility, migration)
  and the api-usage/api-surface inventories live on the package-split
  branches and land in separately reviewed PRs; shared modules here are
  byte-identical to those branches.

## The freeze manifest: `package-freeze.toml`

One `[[frozen]]` entry per extraction:

- `extraction_revision` — the last main revision carrying the pre-extraction
  content (the gate reads content from this revision; it must exist on main).
- `source_paths` — protected paths.
- `[frozen.hashes]` — SHA-256 of each protected path's content at the
  extraction revision (manifest integrity).
- `[[frozen.approved_rewrites]]` — authorized changes, each pinning the
  **exact** approved content: `path`, `reason`, `approver`, `sync_record`
  and `post_hash` (SHA-256 of the file content the rewrite authorizes). A
  path-scoped approval alone would pre-authorize every future edit; the
  gate rejects manifests without `post_hash`, and the head content must
  match one of a path's pinned hashes.
- `[[freeze_exceptions]]` — time-boxed exceptions with ISO `expiry`.

## How the gate runs

- **Checks:** `freeze-manifest` (schema + hash integrity, read from the
  `--base` revision so a PR cannot weaken its own rules), `source-freeze`
  (drift between `extraction_revision` and `--head`: unauthorized
  adds/deletes/renames/edits; rewritten paths tolerated only when the head
  content matches a pinned `post_hash`), `approved-rewrites` (evidence).
- **In CI:** `.github/workflows/package-split-freeze-guard.yml` runs on
  `pull_request_target` for every PR once this file is on the base branch —
  harness and rules are always taken from the PR's **base** branch, and the
  PR head is only inspected (diff/hashes), never executed.
- **Locally:** run against your branch head, e.g.
  `./scripts/package-split freeze --base origin/main --head HEAD`.

## Adding or changing a freeze

- New extraction: add a `[[frozen]]` entry (the freeze-first PR for that
  module) and merge it **before** moving any code.
- Legitimate change to a frozen path (e.g. an upstream typing fix during the
  window): add an `[[frozen.approved_rewrites]]` entry with the exact
  `post_hash` of the approved content and a sync record; the extracted copy
  in datadog-apm-python absorbs the change in the same window.
- Tests: `tests/package_split/test_freeze.py` on the package-split branches
  (drift, second-edit-after-rewrite, post_hash validation, exceptions).
