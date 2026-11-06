---
name: architecture-review
description: >
  Review a change against dd-trace-py's architectural constraints: fork safety and background
  threads, dependency direction, and import cycles. Use this before committing changes that
  touch threads, locks, fork hooks, services, writers, remote config callbacks, module layout
  or cross-package imports, and when asked to review someone else's PR or branch for
  architectural issues.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
---

# Architecture review

The constraints are in two checklists, each with trigger symbols, stop conditions and rules
identified by ID:

- [references/fork-safety.md](references/fork-safety.md): fork hooks, threads, locks, startup
  of background work (`FS-*`).
- [references/layering.md](references/layering.md): dependency direction, cycles, shared
  infrastructure (`LA-*`).

## 1. Get the diff

- Your own change: `git diff $(git merge-base origin/main HEAD)` (includes uncommitted work).
- A PR: `gh pr diff <number>`. To run the detectors, check the PR out in a separate worktree
  (`git fetch origin pull/<number>/head` then `git worktree add <tmpdir> FETCH_HEAD`) and keep
  your own working tree untouched.
- A branch or a commit range: `git diff <base>...<head>`.

Review only what the change adds or modifies. Existing code that breaks a rule is out of scope
unless the change copies the pattern, extends it, or makes it worse.

## 2. Select the checklists

Search the added lines and the touched files for each checklist's trigger symbols. Read every
checklist that matches. If neither matches, say so and stop.

## 3. Run the tools

- `scripts/lint sg <changed files under ddtrace/ and tests/>`: the ast-grep rules cover part of
  FS-4, FS-13 and FS-14.
- If the layering checklist applies: run the base and candidate comparisons described in the
  `dependency-direction-analysis` and `circular-import-analysis` skills.

Tool output is evidence, not the review. Most rules (for example FS-2, FS-6, FS-7, FS-10,
LA-4) need reading code.

## 4. Check the rules

For each changed hunk that matches a trigger, read enough of the surrounding code to answer
the rule's question. Look at the thread the code runs on (application thread, ddtrace thread,
fork hook, GC callback), the locks it takes, and whether it waits on anything. Follow calls
into other modules when the answer depends on them.

The checklists are not exhaustive. If a change conflicts with the intent described in the
"Fork Safety" section of `ddtrace/internal/README.md`, in `docs/native-code-review.md`, or in
`.cursor/rules/isolated-responsibility.mdc`, report it as a question.

## 5. Report

List the findings most severe first. For each finding give:

- the rule ID and `file:line`
- what the code does
- a concrete failure scenario (for example: "a Gunicorn worker forks while the RC thread holds
  `_lock`; the child's after-fork hook waits on it forever")
- the fix the rules suggest

Use three severities:

- **Blocker**: one of a checklist's stop conditions.
- **Violation**: breaks a rule, without being a stop condition.
- **Question**: depends on intent the diff does not show, or extends an existing exception to a
  **target** rule.

End with the checklists applied, the tools run and their results. If nothing was found, say so
explicitly.

When reviewing your own change, fix blockers and violations before committing. When reviewing
someone else's PR, report to the user. Do not post PR comments unless asked.
