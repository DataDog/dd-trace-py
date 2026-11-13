# Package split harness scope

The source harness now keeps API consumer inventories and source freeze
enforcement. Six generic gates and their build, virtual environment, probe and
matrix helpers are removed. The existing import-analysis and wheel pipelines
remain responsible for their checks; extracted package tests and concrete
consumer tests establish the additional extraction guarantees.

The investigation at `f9cda8db85` found that the root boundaries invocation
rejected 11 existing strongly connected components while allowing every one of
18,008 import edges under a permissive policy. Its workspace-layout fixture
also preserved a known false pass. The root lifecycle invocation failed before
checking side effects because its environment lacked envier. The contracts
manifest pinned only the type of `ddtrace.__version__`, and no real extraction
matrices exercised compatibility or migration.

Those defaults did not justify a second general-purpose validation framework.
The [CLI reference](../../scripts/package_split/README.md) maps each removed
command to its replacement or the concrete coverage still required for an
extraction. Sdist parity, native-free dependency closure, dynamic imports and
actual instrumentation counts remain explicit requirements when applicable;
ordinary wheel smoke tests do not establish them.

## Retained guardrails

Consumer inventories follow re-export chains, handle source encodings, report
unparsable files and support workspace source layouts. Unknown targets fail
unless a consumer-only scan is explicitly requested. Evidence is separated by
scope and target so one scan cannot overwrite another. These changes reuse the
existing inventory implementation and fixtures rather than adding a new scanner.

Freeze manifests use schema version 2. Each approved rewrite pins its exact
replacement hash, and the candidate must match the approval from the protected
base. Later edits, deletions and reversions fail, including when a PR changes its
own freeze file. Evidence hashes the protected rules rather than the candidate's
copy. The source workflow no longer requires a package contract just to enforce
an extraction freeze.

## Compatibility and ownership

Existing source commands for artifacts, boundaries, lifecycle, contracts,
compatibility and migration are retired. Package build and runtime metadata have
one executable source of truth in the destination. Keep the reviewed extraction
scope, ownership and rollback decisions with the extraction, and encode actual
consumer scenarios in the existing test suites.

This change does not extract or release a package. Each extraction must still
prove its supported behavior with exact artifacts before cutover. The earlier
[codeowners trial](codeowners-port-trial-2026-11.md) records historical harness
behavior; its old commands are superseded by the current CLI reference.
