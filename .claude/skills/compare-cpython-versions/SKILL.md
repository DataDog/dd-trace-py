---
name: compare-cpython-versions
description: >
  Compare CPython source code between two Python versions to identify changes in
  headers and structs. Use this when adding support for a new Python version to
  understand what changed between versions.
allowed-tools:
  - Bash
  - Read
  - Grep
  - WebSearch
  - TodoWrite
---

# Compare CPython versions

Use the [CPython dependency inventory](../find-cpython-usage/SKILL.md) to scope this work.
Resolve exact old/new tags or commits; distinguish a release tag from a moving development branch.
Use an existing CPython checkout when available, or a separate checkout of
`https://github.com/python/cpython`. Do not switch the user's working checkout to inspect another ref.

Run in the CPython checkout, substituting verified refs and paths:

```bash
git diff <old-ref> <new-ref> -- <relevant-header-or-source>
git log <old-ref>..<new-ref> -- <relevant-header-or-source>
git log -p <old-ref>..<new-ref> -S '<changed-symbol>' -- Include Python Objects
```

Inspect implementations as well as declarations: layouts, ownership, GIL requirements,
finalization, and error behavior can change without a signature change. Use the exact revision's
source, What's New notes, and linked upstream issues to explain each relevant difference.

Report affected ddtrace callers, the changed contract, required adaptation, and validation across
the supported versions/platforms. Treat pre-release behavior as version-specific; do not turn a
snapshot into an undated universal rule.
