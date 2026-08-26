---
name: releasenote
description: >
  Decide whether a release note is needed, and if so create or update a Reno fragment,
  following dd-trace-py's conventions (docs/releasenotes.rst).
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
---

## Decide if a note is needed

Required: breaking API change, new feature, bug fix, deprecation, dependency upgrade —
if it's customer-impacting.
Not required: CI chores, internal-only/not-yet-released API changes, test-only changes.

If customer impact is unclear, **ask the user** what the customer impact is. Never guess —
unless the PR/issue/ticket you were given already states it. If no note is needed, say so and
suggest the `changelog/no-changelog` PR label instead.

## Audience

The note is for a customer upgrading dd-trace-py — never for a dd-trace-py contributor.
It is not a PR summary. Internal rationale, implementation detail, and CI/test notes go in
the PR description, not here.

## Before writing

1. If the category (`features`/`fixes`/`upgrade`/`deprecations`/`issues`/`api`/`security`/`other`)
   isn't given, ask.
2. Check for an existing fragment on the same topic, scoped to your current branch's own
   unmerged changes: `git diff --name-only <base>...HEAD -- releasenotes/notes/`, where
   `<base>` is the branch you're merging into (usually `origin/main`; use the actual base if
   this branch forked off a release branch instead). Only reuse a fragment from that diff — one
   already on the base branch may already be released; create a new fragment instead of
   editing a shipped one.
3. Read the PR/commit diff for ground truth, but translate it — don't copy engineering language.

## Write the note

Generate the skeleton: `riot run reno new <slug>` (slug: lowercase, hyphenated, e.g.
`fix-aioredis-catch-canceled-error`). Edit the generated YAML, keeping only the section(s)
that apply — one Reno fragment per change.

Format: `<scope>: <sentence(s)>.` Scope is the component name (see
:ref:`release_notes_scope` in docs/releasenotes.rst — `tracing`, `profiling`, `ASM`,
`dynamic instrumentation`, `CI visibility`, or the integration name). Use `internal` when the
change genuinely isn't tied to one product (e.g. core threading/fork-safety affecting several
products) — that's a legitimate scope, not a placeholder for when you didn't pick one.

**Fix** — state the customer-visible symptom, not the root cause, in present tense:
`Fixes an issue where <symptom> occurs when <condition>.`

**Feature/behavior change** — if it's configurable or changes a default, state the exact env
var / config option / argument and what it does. Don't skip this when a toggle exists.

**Never include**: internal function/class names, internal tag prefixes (`_dd.*`),
RFC/JIRA/ticket numbers, CI job names, draft leftovers (`TODO`, `XXX`, unfinished sentences),
or "why we built it this way" rationale.

**Length**: 1-2 sentences. Longer only to show a code example for a new public API.

**Verify facts against the code**, not the PR title — defaults and numbers drift.

## Examples

Good (fix):
```yaml
fixes:
  - |
    profiling: Fixes inconsistent handling of ``DD_PROFILING_MAX_FRAMES`` by clamping it
    before stack collection so samples stay within the backend's 600-location limit.
```

Good (feature, toggle):
```yaml
features:
  - |
    AI Guard: Adds per-integration kill switches ``DD_AI_GUARD_ANTHROPIC_ENABLED`` and
    ``DD_AI_GUARD_LANGCHAIN_ENABLED`` (default ``true``). Set to ``false`` to disable AI
    Guard for that provider/framework.
```

Bad → good rewrite (`internal` scope is fine here — not product-specific; the fix is the wording):
```yaml
# Bad: internal mechanism, no customer symptom
fixes:
  - |
    internal: Fixed a GC reference-cycle leak in PeriodicThread.

# Good
fixes:
  - |
    internal: Fixes a memory leak that causes memory usage to grow over time in
    long-running processes using background periodic tasks.
```
