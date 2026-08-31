# Git Hooks

This project uses [Autohook](https://github.com/Autohook/Autohook) to manage git hooks.

## Installation

To install all git hooks, run:

```bash
hooks/autohook.sh install
```

This will create symlinks in `.git/hooks/` for all configured hook types.

On Datadog-managed laptops (`core.hooksPath=/usr/local/dd/global_hooks`), **do not**
set a local `core.hooksPath` override. That bypasses `dd-git-hooks` secrets scanning.
`hooks/autohook.sh install` removes a stale local `.git/hooks` override automatically.
After install, commits run:

```
global pre-commit → dd-git-hooks (secrets) → run-local-hooks → autohook (lint/format)
```

If you previously used `git config --local core.hooksPath .git/hooks`, re-run
`hooks/autohook.sh install` or `hooks/scripts/ensure-dd-hook-chain.sh`.

### Verifying the global hook chain (DD laptops)

Unit tests cover `ensure-dd-hook-chain.sh` logic. To manually verify wiring and
(optionally) secrets scanning on a Datadog laptop:

```bash
hooks/scripts/verify-dd-hook-chain.sh           # doctor + bypass/chain checks (+ repair if present)
DD_HOOK_VERIFY_EXAMPLE_SECRET='...' hooks/scripts/verify-dd-hook-chain.sh --secrets
```

Or via hook tests: `DD_HOOK_CHAIN_INTEGRATION=1 bash scripts/run-hook-tests`

For `--secrets`, set `DD_HOOK_VERIFY_EXAMPLE_SECRET` to a **non-production**
test credential (e.g. a TruffleHog documentation example). The probe may **skip**
if `dd-git-hooks` only blocks verified secrets or if the local scanner needs
attention (`dd-git-hooks -doctor`).

## Available Hooks

### pre-commit (blocking)
Runs before each commit. A non-zero exit code **aborts the commit**. Contains:
- Code formatting and linting checks
- Other pre-commit validations

Individual pre-commit hooks (run in numeric order):

| Hook | Description |
|------|-------------|
| `01-format-and-lint` | Formats and lints staged Python files |
| `02-run-mypy` | Type-checks staged Python files |
| `03-run-codespell` | Spell-checks staged files |
| `04-run-clang-format` | Formats staged C/C++ files with clang-format |
| `05-run-bandit` | Security-scans staged production Python files |
| `06-check-cython-stubs` | Validates Cython stub files |
| `07-run-cmake-format` | Formats staged CMake files (`*.cmake`, `CMakeLists.txt`) |
| `08-run-sg` | Runs `ast-grep scan` on staged Python files using rules in `.sg/rules/`. Catches anti-patterns and deprecated API usage. Skipped when no Python files are staged. |
| `09-run-error-log-check` | Checks that `log.error()`, `add_error_log`, and `iast_error` calls use constant string literals as their first argument (LOG001) |

### post-merge (non-blocking)
Runs after `git pull` or `git merge`. Non-zero exit codes are logged but **do not block** the operation (the merge has already completed). Contains:
- **check-native-changes**: Detects changes to native code files (C, C++, Rust, Cython) and Python dependency files, and alerts you to rebuild or reinstall

### post-checkout (non-blocking)
Runs after `git checkout` or `git switch`. Non-zero exit codes are logged but **do not block** the operation. Contains:
- **check-native-changes**: Detects changes to native code files and Python dependency files, and alerts you to rebuild or reinstall

## Native Code Change Detection

### Problem
When you checkout a branch, merge changes, or pull from main that includes modifications to native code files, your compiled `.so` files may become out of date. This can cause:
- Segmentation faults
- Import errors
- Unexpected behavior
- Cryptic error messages

### Solution
The `check-native-changes` hook automatically detects when native code files have changed and reminds you to rebuild.

### Monitored File Types
- `*.c`, `*.cpp`, `*.h`, `*.hpp` - C/C++ files
- `*.rs` - Rust files
- `*.pyx`, `*.pxd` - Cython files
- `Cargo.toml`, `Cargo.lock` - Rust dependencies
- `setup.py`, `pyproject.toml` - Build configuration
- `requirements*.txt` - Python dependency files

### Example Output
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️  NATIVE CODE CHANGES DETECTED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The following native code files have changed:

  • ddtrace/profiling/collector/stack.c
  • ddtrace/internal/_encoding.c
  • ddtrace/appsec/_ddwaf.cpp
  ... and 5 more file(s)

You may need to rebuild native extensions.

Run one of the following commands:

  # Quick rebuild (recommended):
    pip install -e .

  # Full clean rebuild:
    pip install -e . --force-reinstall --no-deps --no-build-isolation

  # Using project scripts:
    scripts/ddtest pip install -e .

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Rebuild Commands

**Quick Rebuild (Recommended):**
```bash
pip install -e .
```
Rebuilds changed native extensions. Fast and usually sufficient.

**Full Clean Rebuild:**
```bash
pip install -e . --force-reinstall --no-deps --no-build-isolation
```
Forces complete rebuild of all extensions. Use when quick rebuild doesn't work.

**Using Docker:**
```bash
scripts/ddtest pip install -e .
```
Rebuilds inside the testrunner container.

### Manual Check
You can manually check for native changes between any two commits:

```bash
# Check changes between commits
hooks/scripts/check-native-changes <old-commit> <new-commit>

# Check changes since last commit
hooks/scripts/check-native-changes HEAD~1 HEAD

# Check changes between branches
hooks/scripts/check-native-changes origin/main HEAD
```

## Adding New Hooks

To add a new hook script:

1. Create the hook type directory if it doesn't exist:
   ```bash
   mkdir -p hooks/<hook-type>
   ```

2. Add your script to that directory:
   ```bash
   touch hooks/<hook-type>/my-script
   chmod +x hooks/<hook-type>/my-script
   ```

3. If it's a new hook type (not pre-commit, post-merge, or post-checkout), add it to `autohook.sh`:
   ```bash
   # Edit hooks/autohook.sh
   hook_types=(
       "pre-commit"
       "post-merge"
       "post-checkout"
       "your-new-hook-type"  # Add here
   )
   ```

4. Run the install command:
   ```bash
   hooks/autohook.sh install
   ```

## Disabling Hooks Temporarily

To temporarily disable all hooks:
```bash
git commit --no-verify  # Skip pre-commit hooks
```

To disable specific hooks:
```bash
chmod -x .git/hooks/<hook-type>
```

To re-enable:
```bash
chmod +x .git/hooks/<hook-type>
```

## Troubleshooting

### Hook Not Running
Check if hooks are installed:
```bash
ls -la .git/hooks/
```

If missing, run:
```bash
hooks/autohook.sh install
```

### Commits skip DD secrets scanning (Datadog laptops)
If `git config --local core.hooksPath` points at `.git/hooks`, git never runs
`/usr/local/dd/global_hooks/pre-commit` and `dd-git-hooks` is skipped. Fix:

```bash
hooks/scripts/ensure-dd-hook-chain.sh
hooks/autohook.sh install
```

Verify the global chain (optional):

```bash
DD_GIT_HOOKS_DEBUG=1 /usr/local/dd/global_hooks/pre-commit
```

You should see `dd-git-hooks` run, then `run-local-hooks` invoking `.git/hooks/pre-commit`.

### Hook Failing
Check the hook scripts are executable:
```bash
ls -la hooks/post-merge/
ls -la hooks/post-checkout/
ls -la hooks/scripts/
```

Make them executable if needed:
```bash
chmod +x hooks/post-merge/*
chmod +x hooks/post-checkout/*
chmod +x hooks/scripts/*
```

### Seeing False Warnings
The native change detection may occasionally warn about changes that don't require a rebuild (e.g., comment changes). In these cases:
1. Ignore the warning if you're confident no rebuild is needed
2. Run a quick rebuild to be safe: `pip install -e .`

## Directory Structure

```
hooks/
├── autohook.sh              # Autohook manager script
├── README.md                # This file
├── pre-commit/              # Pre-commit hooks
│   ├── ...
│   └── 08-run-sg            # ast-grep scan on staged Python files
├── post-merge/              # Post-merge hooks
│   ├── 00-ensure-dd-hook-chain # Symlink → scripts/run-ensure-dd-hook-chain-quiet.sh
│   └── check-native-changes # Detects native code and dependency changes
├── post-checkout/           # Post-checkout hooks
│   ├── 00-ensure-dd-hook-chain # Symlink → scripts/run-ensure-dd-hook-chain-quiet.sh
│   └── check-native-changes # Detects native code and dependency changes
└── scripts/                 # Shared scripts
    ├── check-native-changes # Native change and dependency detection logic
    ├── ensure-dd-hook-chain.sh # DD laptop global-hook chain guard
    ├── run-ensure-dd-hook-chain-quiet.sh # Shared post-checkout/post-merge entrypoint
    └── verify-dd-hook-chain.sh # Manual DD laptop hook-chain verifier
```

## For Contributors

When adding or modifying native code:
1. Ensure the file extension is in the monitored patterns
2. Test that the hook detects your changes:
   ```bash
   hooks/scripts/check-native-changes HEAD~1 HEAD
   ```
3. Update `hooks/scripts/check-native-changes` if you add new file types

## References

- [Autohook Documentation](https://github.com/Autohook/Autohook)
- [Git Hooks Documentation](https://git-scm.com/book/en/v2/Customizing-Git-Git-Hooks)
