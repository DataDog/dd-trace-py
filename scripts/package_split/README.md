# Package-split CLI

`scripts/package-split [--manifest package-contract.toml] [--output .package-split] <command> [options]`; `--help` lists options.
Gates: `boundaries`, `lifecycle`, `artifacts`, `contracts --baseline <wheel>`, `compatibility --matrix <toml>`, `migration --matrix <toml>`, `freeze --base <rev> [--head <rev>]`.
Inventories: `api-usage --module <target> [--source-path <path> ...]`, `api-surface`; static approximations, not compatibility proofs.
Exit 0/pass, 1/fail (required `not_run` fails), 2/usage or contract error. Evidence: `<output>/<gate>/<gate>.json` (revision, manifest hash, environment, checks/skips).
Schemas/examples: root `package-contract.toml`, `matrices.py`, `freeze_manifest.py`. Add negative fixtures in `tests/package_split/`; use `scripts/run-tests` and `scripts/lint`. Extend registry/CLI, version schema changes. [Plan](../../docs/package-split/plan.md).

[Harness audit](../../docs/package-split/harness-audit-2026-11.md): proposed ownership, overlap with existing checks, and gaps to resolve before trimming gates.
