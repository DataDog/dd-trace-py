---
name: lint
description: >
  Run targeted linting, formatting, and code quality checks on modified files.
  Use this to validate code style, type safety, security, and other quality metrics
  before committing. Supports running all checks or targeting specific checks on
  specific files for efficient validation.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - TodoWrite
---

# Formatting and checks

Run `scripts/lint` from the repository root. It provisions the versions in
`pyproject.toml`'s lint dependency group. Do not invoke the underlying tools directly.

```bash
scripts/lint fmt -- <edited-python-file>
scripts/lint typing -- <edited-python-file>
scripts/lint spelling -- <edited-document>
scripts/lint checks
```

Format edited code, use focused checks during iteration, and run `checks` before committing.
Do not pass Markdown to the Python formatter. Native formatters invoked by `fmt`/`style` have
their own selection logic; inspect the diff afterward rather than assuming every check is file-scoped.

## Discover specialized checks

`scripts/lint --help` is the current command inventory. Useful task-specific checks include:

| Change | Subcommand |
|--------|------------|
| Updated trace snapshots | `fmt-snapshots` |
| Python handling user input, credentials, or sensitive operations | `security` |
| New test files / suitespec | `suitespec-check`, `suitespec-duplicates` |
| Microbenchmark scenarios or thresholds | `slo-ownership` |
| Profiling native coverage | `profiling-native-check` |
| Environment-variable registry | `supported-config-check` |
| Log messages | `error-log-check` |
| Type annotations | `typing`, `type-comments-check` |
| ast-grep rules | `sg`, `sg-test` |
| Git hooks | `hook-tests` |

After changing annotations, run `typing` and check for new PEP 484 type comments. Use inline
annotations for new types; existing type comments may stay, and `# type: ignore` is allowed.
See `scripts/check_type_comments.py` for the exact diff-based policy and exceptions.

```bash
scripts/lint type-comments-check --base-ref origin/main
scripts/lint fmt-snapshots <edited-snapshot-file-or-directory>
scripts/lint security -r <affected-python-directory>
```

Use the PR's actual base ref for the type-comment check; this check is not currently part of
`scripts/lint checks`. Snapshot formatting rewrites the selected files; add `--check` for
validation only. The full `checks` command already includes the security scan.

Arguments are passed to each underlying command; consult its interface rather than assuming
all subcommands accept paths. `checks` stops at the first failure. Report that failure and any
checks not reached; do not describe a partial run as passing. Avoid unrelated cleanup.
