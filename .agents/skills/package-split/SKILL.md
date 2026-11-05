---
name: package-split
description: Extract packages or run package-split gates and API inventories.
---
# Package split

Read [the plan](../../../docs/package-split/plan.md) and [CLI reference](../../../scripts/package_split/README.md); follow AGENTS.md.

1. Inventory `api-usage --module <target>` (production, then tests) and `api-surface`; propose a narrow contract and obtain scope/name approval.
2. Establish gates and negative fixtures before moving code; port the harness to the destination.
3. Extract incrementally; bridges fall back only for an absent provider, never a broken installation. Preserve canonical identity and record bridge ownership/removal conditions.
4. Run applicable gates against exact artifacts; freeze only after boundary validation. Never weaken baselines or claim unrun checks passed.
5. Report changed boundaries, commands/results/evidence, blockers, compatibility/freeze status and rollback. No publishing or irreversible cutover without approval.
