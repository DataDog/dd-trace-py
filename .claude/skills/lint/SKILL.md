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
| New test files / suitespec | `suitespec-check`, `suitespec-duplicates` |
| Microbenchmark scenarios or thresholds | `slo-ownership` |
| Profiling native coverage | `profiling-native-check` |
| Environment-variable registry | `supported-config-check` |
| Log messages | `error-log-check` |
| Type annotations | `typing`, `type-comments-check` |
| ast-grep rules | `sg`, `sg-test` |
| Git hooks | `hook-tests` |

Arguments are passed to each underlying command; consult its interface rather than assuming
all subcommands accept paths. `checks` stops at the first failure. Report that failure and any
checks not reached; do not describe a partial run as passing. Avoid unrelated cleanup.
