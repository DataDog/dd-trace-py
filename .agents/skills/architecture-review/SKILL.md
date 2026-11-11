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

The rules are in [docs/contributing-architecture.rst](../../../docs/contributing-architecture.rst),
in two groups identified by ID: fork safety and background threads (`FS-*`) and layering and
imports (`LA-*`). This skill adds what a review needs on top of them: which group a change
triggers, which violations block it, and how to report.

## 1. Get the diff

- Your own change: `git diff $(git merge-base origin/main HEAD)` covers committed and
  uncommitted edits to tracked files. It skips new files you haven't staged, so also review
  every path from `git ls-files --others --exclude-standard`, or run `git add -N <paths>` first
  so the diff includes them.
- A PR: `gh pr diff <number>`. To run the detectors, check the PR out in a separate worktree
  (`git fetch origin pull/<number>/head` then `git worktree add <tmpdir> FETCH_HEAD`) and keep
  your own working tree untouched.
- A branch or a commit range: `git diff <base>...<head>`.

Review only what the change adds or modifies. Existing code that breaks a rule is out of scope
unless the change copies the pattern, extends it, or makes it worse.

## 2. Select the rule groups

Search the added lines and the touched files for each group's trigger symbols, and read the
matching sections of `docs/contributing-architecture.rst`. If neither group matches, say so and
stop.

### Fork safety (`FS-*`) triggers

- `os.fork`, `os.register_at_fork`, `multiprocessing`, `forksafe.`, `_before_fork`, `_after_fork`,
  `__autorestart__`, `autorestart=`
- `threading.Thread`, `threading.Timer`, `ThreadPoolExecutor`, `PeriodicService`,
  `AwakeablePeriodicService`, `PeriodicThread`, `threads.Thread`, `periodic.Timer`
- `.start()` / `.stop()` / `.join()` / `.awake()` on a ddtrace service or thread
- `Lock(`, `RLock(`, `Event(`, `Condition(`, `Queue(`, `weakref.finalize`, `__del__`
- `periodic()`, `on_shutdown()`, `reset()`, `flush`, writers, uploaders, remote config callbacks
- Imports inside functions that run on ddtrace threads, fork hooks or remote config callbacks

Stop conditions:

1. `os.register_at_fork` called outside `ddtrace/internal/forksafe.py` (FS-1).
2. I/O, queue flushing or network calls in an after-in-child hook (FS-2).
3. A raw `threading.Thread`, `threading.Timer` or executor started by ddtrace code (FS-3).
4. Two ddtrace threads synchronizing with each other, through a shared lock or by one waiting
   on the other (FS-6).
5. A blocking call without a timeout in `periodic()` or another thread target (FS-7).
6. Application code that waits on a ddtrace thread: a blocking `awake()`, `join()` or queue
   handoff on a request path (FS-10).
7. Operating on an inherited lock in the child instead of recreating it (FS-4).

### Layering (`LA-*`) triggers

- adds, moves or renames a module or package under `ddtrace/`
- adds an import that crosses a top-level package boundary (for example
  `ddtrace/internal/** -> ddtrace/appsec/**`, `ddtrace/contrib/** -> ddtrace/llmobs/**`, or
  `ddtrace/debugging/** -> ddtrace/profiling/**`), including tracing importing contrib
  (`ddtrace/_trace/**` or `ddtrace/trace/** -> ddtrace/contrib/**`)
- adds a function-local import or an `if TYPE_CHECKING:` import
- changes `scripts/import-analysis/layers.json`
- adds a product name, product attribute or product setting inside `ddtrace/internal/`,
  `ddtrace/_trace/` or `ddtrace/contrib/`

Stop conditions:

1. A new violation or a new uncovered module reported by `layers.py compare` (LA-1, LA-7).
2. A new cycle reported by `cycles.py compare` (LA-2).
3. A function-local or `TYPE_CHECKING` import added to avoid a cycle or a layering violation
   (LA-3).
4. A new exception in `layers.json` without an architectural justification for human review
   (LA-7).
5. Tracing code importing contrib (LA-1). The detector does not report this edge.

## 3. Run the tools

- `scripts/lint sg <changed files under ddtrace/ and tests/>`: the ast-grep rules cover part of
  FS-4, FS-13 and FS-14.
- If the layering rules apply: run the base and candidate comparisons described in the
  `dependency-direction-analysis` and `circular-import-analysis` skills.

Tool output is evidence, not the review. Most rules (for example FS-2, FS-6, FS-7, FS-10,
LA-4) need reading code.

## 4. Check the rules

For each changed hunk that matches a trigger, read enough of the surrounding code to answer
the rule's question. Look at the thread the code runs on (application thread, ddtrace thread,
fork hook, GC callback), the locks it takes, and whether it waits on anything. Follow calls
into other modules when the answer depends on them.

The rules are not exhaustive. If a change conflicts with the intent described in the
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

- **Blocker**: one of the stop conditions in step 2.
- **Violation**: breaks a rule, without being a stop condition.
- **Question**: depends on intent the diff does not show, or extends an existing exception to a
  **target** rule.

End with the rule groups applied, the tools run and their results. If nothing was found, say so
explicitly.

When reviewing your own change, fix blockers and violations before committing. When reviewing
someone else's PR, report to the user. Do not post PR comments unless asked.
