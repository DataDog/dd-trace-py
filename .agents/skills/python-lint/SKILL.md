---
name: python-lint
description: >-
  Turn a recurring, mechanically detectable code-review theme into one blocking
  lint or static-analysis check for dd-trace-py, preferring an ast-grep rule in
  `.sg/rules/`. Use when adding or proposing a lint rule, encoding a review
  comment as a check, writing an ast-grep rule, extending a `scripts/check_*.py`
  checker, or when the Thematic Rule Suggestions bot prepares a PR.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - Edit
  - Write
---

# Add a Python lint rule

Grow the blocking lint surface **one check at a time**, in the tool that already owns that
kind of check. Do not add a new tool, a new CI job, or a bespoke script when an existing
mechanism can express the rule. Maintainers have rejected "a deluge of hand-rolled scripts
where proper static analysis / ast-grep exists" (see Lessons below).

Run everything through `scripts/lint` (see the `lint` skill); never invoke ruff or ast-grep
directly.

## 1. Decide whether the theme is a rule at all

Propose a rule only when **all** hold:

- **Recurring**: raised by a human reviewer on 2+ distinct PRs (or one PR plus a clear
  documented convention), and **accepted** (the author changed the code). Bot-only comments,
  unresolved threads, and open PRs do not count.
- **Mechanical**: a yes/no decision from syntax, a path, or a name. No product or design
  judgment (release-note quality, naming taste, "is this abstraction right").
- **Documented**: it enforces something already stated in `AGENTS.md`, `docs/`, or a skill,
  or you add that documentation in the same PR.
- **Not already enforced**: check section 2 and the existing rule/check lists first. If an
  existing check covers it, report that instead of adding a duplicate.
- **Low blast radius**: the whole-repo scan (or diff-scoped check) has zero or a handful of
  hits. A rule that fails hundreds of existing sites needs a human decision first: leave it
  off, name the files, and ask.

If any item fails, do not open a PR. Report the theme and why it was not automatable.

## 2. Pick the tool (stop at the first fit)

| # | Tool | Use when | Where |
|---|------|----------|-------|
| 1 | Already enforced | An existing rule or check covers it | see inventory below |
| 2 | **ruff rule** | A stock ruff rule implements it | `[tool.ruff.lint] select` / `per-file-ignores` in `pyproject.toml` (ruff is pinned there; `preview` rules need `explicit-preview-rules`) |
| 3 | **ast-grep rule** (default for new syntactic checks) | Shape of Python code: calls, attribute access, decorators, imports, comments, missing decorators | `.sg/rules/<id>.yml` + `.sg/tests/<id>-test.yml` |
| 4 | **Datadog static analysis** | Generic best-practice/security rule from a Datadog ruleset | `static-analysis.datadog.yml` (only to add/adjust `ignore:` for an existing ruleset rule; custom rules are not authored here) |
| 5 | **Extend an existing `scripts/check_*.py`** | Needs the git diff (new-lines-only), file metadata, or cross-file data | existing script + `tests/internal/test_check_*.py` |
| 6 | New `scripts/check_*.py` | Last resort: needs a diff or non-code data (JSON, TOML, lockfiles, CSV) that ast-grep/ruff cannot express | follow `scripts/check_no_new_aidev_anchors.py` |

Do not scan Python source with regex or `grep` in a script when an ast-grep pattern can match
the AST. Regex is for non-Python data. Comments can be matched in ast-grep with
`kind: comment`.

### What runs today (verify; this list drifts)

- `scripts/lint style` → ruff format check + `ruff check` (selected: `A D E F G I W UP PLW0244`)
- `scripts/lint typing` → mypy (`mypy.ini`)
- `scripts/lint spelling` → codespell (`docs/spelling_wordlist.txt`)
- `scripts/lint security` → bandit
- `scripts/lint sg` / `sg-test` → ast-grep (`sgconfig.yml`; rules in `.sg/rules`)
- `scripts/lint error-log-check`, `supported-config-check`, `suitespec-check`,
  `slo-ownership`, `profiling-native-check`, `type-comments-check`
- Diff-scoped CI scripts: `scripts/check_no_new_aidev_anchors.py`, `check_type_comments.py`,
  `check_added_file_size.py`, `check_constant_log_message.py`, `check_lockfile_cooldown.py`,
  `check_libdatadog_pins.py`, `check-releasenotes`
- Datadog static analysis ruleset config: `static-analysis.datadog.yml`

Run `ls .sg/rules scripts/check*` and `scripts/lint --help` for the current inventory before
deciding. Read the existing rules in the same area; extend one rather than adding a sibling.

## 3. ast-grep workflow

ast-grep is wired into the `ast-grep rules` job in `.gitlab-ci.yml` and `scripts/lint checks`.
A new rule needs no CI or `scripts/lint` change. Do not touch the runner.

1. Write `.sg/rules/<kebab-id>.yml`. Model it on `span-set-tag-measured.yml` (pattern + `fix:`)
   or `fork-without-subprocess-marker.yml` (structural `has`/`not`/`inside`).
   - `id` equals the filename; `message` states the fix in one line; `language: python`.
   - `note:` gives rationale, a Bad/Good example, and a pointer to the documented convention.
   - Scope with `files:` and `ignores:`. Exclude `ddtrace/vendor/**`,
     `ddtrace/appsec/_iast/_taint_tracking/_vendor/**`, generated `*_pb2.py`, `*.pyi` when the
     rule is about runtime code, and test fixtures that exist to exercise the pattern.
   - Add `fix:` only when the rewrite is always semantically safe.
2. Write `.sg/tests/<kebab-id>-test.yml` with `valid:` and `invalid:` snippets. Include
   near-misses: the construct inside a comment, a string, a docstring, and an allowed sibling
   API. Snapshots under `.sg/tests/__snapshots__/` are generated; refresh with
   `scripts/lint sg-test --update-all` and
   review the diff.
3. Validate:

   ```bash
   scripts/lint sg-test
   scripts/lint sg            # whole-repo scan; inspect every hit for the new rule
   ```

4. Severity: new rules start at `warning` unless the whole-repo scan has zero hits for the rule
   (then `error` is fine and preferred, since it blocks). Never raise to `error` with known
   hits. Fix legitimate hits in the same PR when the cleanup is small and mechanical;
   otherwise stop and report.
5. Exceptions: prefer a narrow `ignores:` entry with a comment over loosening the pattern.

## 4. Extending a `scripts/check_*.py` checker

Only when ast-grep and ruff cannot express it (typically needs `git diff` against a base).

- Reuse the argument handling (`--base-ref`) and diff helper in the nearest existing script.
  Check **added lines only** so existing violations do not fail CI.
- Add or extend `tests/internal/test_check_<name>.py`. Run it through `scripts/run-tests`
  (see the `run-tests` skill); never run pytest directly.
- Wire into an **existing blocking** job (`.gitlab-ci.yml` job for the script, and its
  `.github/workflows/` counterpart if one exists). Do not add a standalone job without
  maintainers asking. Never `allow_failure`.
- Python 3 stdlib only; no new dependencies.

## 5. Documentation and ownership

- Update `.agents/skills/lint/SKILL.md` if a new `scripts/lint` subcommand is added.
- `.agents/` and `.sg/` are owned by `@DataDog/apm-python-guild` via CODEOWNERS; new scripts
  and tests need an owner entry if CODEOWNERS does not already cover the path.
- Add a release note only if the change affects shipped code. Lint/CI/tooling-only PRs use the
  `changelog/no-changelog` label and **no** release note (see the `releasenote` skill).

## 6. Lessons from earlier automated rule PRs

All four prior PRs from the `dd-apm-ecosystems` bot were closed unmerged. Avoid repeating:

- **Bespoke script instead of ast-grep** (rST markup in private docstrings, #21010, #20929):
  reviewers asked who owns the script and why it was not proper static analysis/ast-grep, and
  asked for it to live in CI. The rST theme was ultimately handled by an ast-grep rule
  (`.sg/rules/no-rst-double-backticks.yml`). Start from ast-grep.
- **Check not wired into blocking CI** (#21010): a check nobody runs is worthless; wire it in
  the same PR, via an existing job.
- **New tool for a small invariant** (libdatadog release-tag pin, #21692): a human landed the
  equivalent as `scripts/check_libdatadog_pins.py` (#21843). Check whether a human or another
  PR is already doing the same before opening one, and search closed PRs for the theme.
- **Check that enforces the opposite of maintainer taste** (reject release notes on internal
  files, #21916): a maintainer commented they had no idea where these PRs come from, and the
  rule was judgment-adjacent (path allowlists drift; CI changes can affect published
  artifacts). Prefer rules with an unambiguous pass/fail and no allowlist to maintain.
- **Evidence-heavy, scope-light**: reviewers need a short PR body. Lead with the theme, the
  2+ linked human review threads that were accepted, the tool chosen and why, the number of
  existing hits (ideally 0), and how it was validated.

## 7. PR checklist

- [ ] Theme recurs on 2+ PRs with accepted human feedback (links in the PR body)
- [ ] No existing rule/check covers it; searched `.sg/rules`, `scripts/check*`, open and
      closed PRs
- [ ] Used ast-grep (or ruff / an existing checker); justified in the body if not
- [ ] Rule + tests with valid, invalid, and near-miss fixtures
- [ ] `scripts/lint sg-test` and `scripts/lint sg` pass; whole-repo hit count stated
- [ ] Touched Python formatted (`scripts/lint fmt -- <files>`); `scripts/lint checks` run
- [ ] Wired into existing blocking CI (automatic for ast-grep)
- [ ] One rule per PR; no unrelated cleanup; no release note for tooling-only changes
- [ ] PR body states the rule's owner (the codeowners of the path) and the escape hatch
      (`ignores:` entry) for intentional exceptions
