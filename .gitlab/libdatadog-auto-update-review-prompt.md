# libdatadog Auto-Update Mechanical-Change Review

An automated script just bumped the pinned libdatadog dependency in this repo
to __TARGET_REV__; the CI pipeline for the bumped branch then failed, and an
AI agent (which could not run cargo — it reasoned from CI traces alone)
repaired it. You are an independent reviewer: verify the result is simple and
mechanical.

Inspect the full working-tree change set with `git status` and
`git diff HEAD` (new files are untracked and only show in `git status`).
Expected mechanical changes: libdatadog `rev` pins in `src/native/Cargo.toml`,
the regenerated `Cargo.lock`, a release note in `releasenotes/notes/`, and
small rename/signature/import adaptations in `src/native`.

PASS only if every change is a simple, mechanical adaptation. FAIL if you see
weakened or deleted tests, assertions, or lints; refactors unrelated to the
bump; behavior changes; or anything that requires a design decision or looks
like working around the failure instead of fixing it.

End your reply with exactly one final line: 'VERDICT: PASS' or
'VERDICT: FAIL: <reason>'.
