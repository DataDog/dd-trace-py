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

# Linting and Code Quality Skill

This skill helps you efficiently validate and format code using the project's comprehensive linting infrastructure.

## When to Use This Skill

Use this skill when you:
- Edit a file and want to format it before committing
- Need to validate code style, types, or security
- Want to check for spelling errors or documentation issues
- Need to validate test infrastructure (suitespec, log messages)
- Want to run comprehensive quality checks before pushing

## Key Principles

1. **Always format after editing** - Use `scripts/lint fmt -- <file>` immediately after code changes
2. **Run comprehensive checks before committing** - Use `scripts/lint checks` before pushing
3. **Target specific files** - Use `-- <file>` syntax to validate only what you changed, not the entire codebase
4. **Fix auto-fixable issues** - Use `fmt` instead of manually fixing style issues
5. **Type check after adding types** - Use `scripts/lint typing -- <file>` after adding type annotations

## Quick Start

**Run all checks (broad validation):**
```bash
scripts/lint checks
```

**Format and validate a specific file:**
```bash
scripts/lint fmt -- path/to/file.py
```

**Check types on a specific file:**
```bash
scripts/lint typing -- path/to/file.py
```

## Available Lint Scripts

### Code Formatting

#### `fmt` - Format code (recommended for most edits)
Formats and validates code style using Ruff.

**Usage:**
```bash
# Format entire codebase
scripts/lint fmt

# Format specific files
scripts/lint fmt -- ddtrace/tracer.py tests/test_tracer.py

# Format specific directory
scripts/lint fmt -- ddtrace/contrib/flask/
```

**What it does:**
1. Runs the Ruff formatter
2. Runs Ruff with --fix to auto-fix issues
3. Re-validates with style checks

**When to use:** After making code changes to automatically format and fix style issues.

#### `fmt-snapshots` - Format snapshot files
Formats snapshot test files used in snapshot-based testing.

**Usage:**
```bash
scripts/lint fmt-snapshots -- tests/snapshots/
```

**When to use:** After snapshot test updates or when snapshot files need reformatting.

### Style Checking

#### `style` - Check all style issues (no auto-fix)
Validates code style without automatically fixing issues.

**Usage:**
```bash
# Check entire codebase
scripts/lint style

# Check specific files
scripts/lint style -- ddtrace/
```

**What it validates:**
- Ruff formatting
- Ruff linting rules
- Cython linting
- C code formatting
- CMake formatting

**When to use:** To verify style compliance before committing without auto-fixes.

#### `format_check` - Check formatting

Validates Python code formatting with `ruff format` (no auto-fix).

**Usage:**
```bash
scripts/lint format_check -- ddtrace/tracer.py
```

**When to use:** Quick check of Python formatting before committing.

### Type Checking

#### `typing` - Type check with mypy
Validates Python type hints and catches type-related errors.

**Usage:**
```bash
# Check all types
scripts/lint typing

# Check specific files (mypy path format)
scripts/lint typing -- ddtrace/tracer.py
```

**When to use:** After adding type hints or modifying functions with type annotations.

### Security Checks

#### `security` - Security audit with Bandit
Scans code for common security issues and vulnerabilities.

**Usage:**
```bash
# Scan entire codebase
scripts/lint security

# Scan specific directory
scripts/lint security -- -r ddtrace/contrib/
```

**When to use:** Before committing code that handles user input, credentials, or sensitive operations.

### Spelling and Documentation

#### `spelling` - Check spelling
Validates spelling in documentation, comments, and docstrings.

**Usage:**
```bash
# Check all spelling
scripts/lint spelling

# Check specific files
scripts/lint spelling -- docs/ releasenotes/
```

**When to use:** Before committing documentation or user-facing text.

### Test Infrastructure

#### `riot` - Validate riotfile
Doctests the riotfile to ensure test venv definitions are valid.

**Usage:**
```bash
scripts/lint riot
```

**When to use:** After modifying `riotfile.py` to validate syntax and doctest examples.

#### `suitespec-check` - Validate test suite specifications
Checks that test suite patterns in `tests/suitespec.yml` cover all test files.

**Usage:**
```bash
scripts/lint suitespec-check
```

**When to use:** After adding new test files or modifying suite specifications.

#### `error-log-check` - Validate error log messages
Ensures error log messages follow project conventions.

**Usage:**
```bash
scripts/lint error-log-check
```

**When to use:** After adding new error logging statements.

### Code Analysis

#### `sg` - Static analysis with ast-grep
Performs static code analysis using ast-grep patterns.

**Usage:**
```bash
# Scan all files
scripts/lint sg

# Scan specific directory
scripts/lint sg -- ddtrace/
```

**When to use:** To find code patterns that may need refactoring or optimization.

#### `sg-test` - Test ast-grep rules
Validates ast-grep rule definitions.

**Usage:**
```bash
scripts/lint sg-test
```

**When to use:** After modifying ast-grep rules or patterns.

### C/CMake Formatting

#### `cformat` - Check C code formatting
Validates C code formatting.

**Usage:**
```bash
scripts/lint cformat
```

**When to use:** After modifying C extension code.

#### `cmakeformat` - Check CMake formatting
Validates CMake file formatting.

**Usage:**
```bash
scripts/lint cmakeformat
```

**When to use:** After modifying CMakeLists.txt or other CMake files.

## Common Workflows

### Workflow 1: Quick File Format and Check
After editing a Python file, format and validate it:

```bash
# Edit the file...
# Then run:
scripts/lint fmt -- path/to/edited/file.py
```

### Workflow 2: Type Check After Adding Types
After adding type hints:

```bash
scripts/lint typing -- ddtrace/contrib/flask/patch.py
```

### Workflow 3: Full Validation Before Commit
Run all checks before creating a commit:

```bash
scripts/lint checks
```

This runs:
- style checks
- typing checks
- spelling checks
- riot validation
- security checks
- suitespec validation
- error log validation
- ast-grep analysis

### Workflow 4: Security Review
Before committing code handling sensitive operations:

```bash
scripts/lint security -- -r ddtrace/contrib/
```

### Workflow 5: Documentation Review
After writing documentation or docstrings:

```bash
scripts/lint spelling -- docs/ ddtrace/
```

## Best Practices

### DO ✅

- **Format files immediately after editing**: Use `scripts/lint fmt -- <file>` to auto-fix style issues
- **Run `checks` before pushing**: Ensures all quality gates pass
- **Target specific files**: Use `-- <file>` syntax to validate only what you changed
- **Check types early**: Run `typing` after adding type annotations
- **Read error messages**: Understand what lint failures mean before fixing

### DON'T ❌

- **Ignore lint failures**: They indicate potential bugs or style issues
- **Manually fix issues that auto-fix can handle**: Use `fmt` instead
- **Commit without running checks**: Let automation catch issues before push
- **Run checks every time for small changes**: Use targeted commands during development

## Passing Arguments

All lint commands support passing arguments with `--` syntax:

```bash
# Basic format
scripts/lint <subcommand> -- <args>

# Examples:
scripts/lint fmt -- ddtrace/tracer.py                    # Format specific file
scripts/lint typing -- ddtrace/                           # Type check directory
scripts/lint security -- -r ddtrace/contrib/              # Security scan with args
scripts/lint spelling -- docs/ releasenotes/              # Spelling check specific paths
```

## Troubleshooting

### Formatting keeps failing
Ensure you've run `scripts/lint fmt` to auto-fix style issues first:
```bash
scripts/lint fmt -- <file>
scripts/lint style -- <file>  # Should now pass
```

### Type errors after editing
Make sure type hints are correct and all imports are available:
```bash
scripts/lint typing -- <file>
```

### Lint command not found
Ensure you're running from the project root:
```bash
cd /path/to/dd-trace-py
scripts/lint checks
```

### Too many errors to fix manually
Use `fmt` to auto-fix most issues:
```bash
scripts/lint fmt -- .
```

## Related

- **run-tests skill**: For validating that changes don't break tests
- **pyproject.toml** `[dependency-groups]`: Source of truth for all lint tool versions
- **riotfile.py**: Defines test venvs and combinations
