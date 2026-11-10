---
name: package-split
description: Extract packages or run package-split gates and API inventories.
---
# Package split

Read [the plan](../../../docs/package-split/plan.md) and [CLI reference](../../../scripts/package_split/README.md); follow AGENTS.md.

1. Inventory `api-usage --module <target>` (production, then tests) and `api-surface`; propose a narrow contract and obtain scope/name approval.
2. Freeze on main before any code moves: land the freeze manifest (package-freeze.toml) and the PR freeze guard on main as the entry point of the extraction. Approved rewrites pin their exact content (post_hash) and carry sync records; absorb upstream changes to frozen paths into the extracted copy in the same window.
3. Establish gates and negative fixtures before moving code; port the harness to the destination.
4. Extract incrementally; bridges fall back only for an absent provider, never a broken installation. Preserve canonical identity and record bridge ownership/removal conditions.
5. Run applicable gates against exact artifacts; never weaken baselines or claim unrun checks passed.
5. Report changed boundaries, commands/results/evidence, blockers, compatibility/freeze status and rollback. No publishing or irreversible cutover without approval.
