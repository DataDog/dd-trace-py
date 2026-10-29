---
name: release
description: >
  Drive dd-trace-py patch, RC, and final releases with scripts/gh/create_gh_release.py.
  Use when asked to patch a PR into a fix release, create an RC, ship X.Y.0, or run
  post-release after a maintainer publishes.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
---

## When to use

| Phrase | Command |
|--------|---------|
| "patch PR N into a fix release" / "cut a patch for #N" | `scripts/gh/create_gh_release.py patch N [--branch X.Y]` |
| "create RC for the next release" / "cut an RC" | `scripts/gh/create_gh_release.py rc [--candidate SHA]` |
| "ship X.Y.0" / "final release for X.Y" | `scripts/gh/create_gh_release.py final X.Y` |
| "post-release" / "version is published, continue" | `scripts/gh/create_gh_release.py post-release vX.Y.Z` |

Building blocks (`bump-rc`, `ensure-branch`, `draft-release`) are also available; prefer the intent commands above.

## How to run

1. Re-run the **same** intent command until it prints `WAITING ON: ...` (exit 2) or completes verification.
2. Report the gate plainly to the human. Do not invent workarounds past tag rulesets, reviews, or auth.
3. Prefer `--dry-run` first when inspecting live GitHub state without creating remotes/PRs/drafts.
4. Never publish a GitHub release. Never push release branches or tags unless the human explicitly asks in that message. The script stops at a **draft**.
5. Never post to Slack as the user; print the review text and stop.
6. `dogfood` and `experiment` are **not implemented** yet — print the checklist the script emits and stop.

## Human gates (typical)

- Fix / backport / bump PR review and merge
- CI green on `origin/X.Y` (or candidate SHA)
- Maintainer publish of the draft (tag ruleset)
- PyPI appearing after the GitLab tag pipeline (`post-release`)
- Dogfooding PRs in dd-source / dogweb (manual until Phase A)

## References

- `docs/contributing-release.rst`
- `scripts/gh/create_gh_release.py --help`
- `scripts/format_release_notes.py`
- Release Procedure review text: `:headsup: The draft release for <VERSION> is ready for review. Can I get a :lgtm:? <LINK>`
