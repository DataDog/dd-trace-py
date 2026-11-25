# Performance

Allocations and CPU cost on hot paths (span creation and finish, tagging, propagation, instrumentation callbacks), at startup (including import time), and in tooling.

This lens is a static review of the frozen diff. Do not run the [run-benchmarks](../../run-benchmarks/SKILL.md) or [profile](../../profile/SKILL.md) skills from this lens, because they execute the changed code. When [AGENTS.md](../../../../AGENTS.md) requires benchmarks for the change and the evidence shows none, report the missing benchmark coverage and recommend `run-benchmarks`.

**When planning:** take the rules below into account when designing the change.

**When reviewing:** report a finding when the diff matches a rule's `Flag:` line on a hot, startup, or high-volume path, or when it has a clear performance problem on such a path that no rule covers (mark it `no rule`). Cite the rule ID and say which path it is on.
