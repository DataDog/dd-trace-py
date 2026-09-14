Override for `reviewers/performance.md` (in the core skill folder) — read that file first, then this.

# Performance — dd-trace-py specifics

This file starts with one confirmed pattern and should grow. Do not treat it as exhaustive.

The source of truth is **AGENTS.md** ("Consider performance impact — this runs in production") and `.cursor/rules/dd-trace-py.mdc` § "Performance First". Apply those as written.

## Hot-path work belongs in C/Cython

Per-request span start/finish, context attach, sampling, and payload encoding are performance-critical. Existing native siblings include `ddtrace/internal/_encoding.pyx` and `ddtrace/internal/_tagset.pyx`. A new per-request Python loop that does that work in pure Python (building dicts/lists on every request) is at least **P1**. Import-time or one-off setup Python is not this finding.
