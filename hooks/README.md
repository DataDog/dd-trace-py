# Git Hooks

This project uses [Autohook](https://github.com/Autohook/Autohook) to manage git hooks.

## Installation

To install all git hooks, run:

```bash
hooks/autohook.sh install
```

This will create symlinks in `.git/hooks/` for all configured hook types.

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
| `10-build-native-ext` | **Warn-only by default** when staged product native/build files change (C/C++/Rust/Cython, CMake, `setup.py`; excludes `test/`, `tests/`, `fuzz/`, `*_test.cpp`). Prints rebuild guidance and does not block the commit. Set `DD_BUILD_NATIVE_ON_COMMIT=1` to run `build_ext --inplace` and block on failure (with `.eggs` retry). Set `DD_SKIP_NATIVE_BUILD=1` to skip entirely. |

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
- **pre-commit**: `10-build-native-ext` **warns** when you commit staged product native or build files (does not block by default). Opt in to a blocking rebuild with `DD_BUILD_NATIVE_ON_COMMIT=1`.
- **post-merge / post-checkout**: `check-native-changes` detects native file changes and reminds you to rebuild after pull or branch switch.

### Pre-commit native hook behavior

| Mode | How to enable | Commit blocked? | What runs |
|------|----------------|-----------------|-----------|
| Warn only (default) | (none) | No | Lists staged product native files and rebuild commands |
| Blocking rebuild | `export DD_BUILD_NATIVE_ON_COMMIT=1` | Yes, if `build_ext` fails | Full `python setup.py build_ext --inplace` |
| Skip | `export DD_SKIP_NATIVE_BUILD=1` or `git commit --no-verify` | No | Nothing |

**Staged paths that trigger the hook** (product code only): `*.c`, `*.h`, `*.cc`, `*.cpp`, `*.hpp`, `*.rs`, `*.pyx`, `*.pxd`, `CMakeLists.txt`, `*.cmake`, root `setup.py`.

**Excluded** (hook does not run for these alone): paths under `test/`, `tests/`, `fuzz/`, `dd_wrapper/test/`, and `*_test.cpp`.

**Timing when blocking rebuild is enabled** (`DD_BUILD_NATIVE_ON_COMMIT=1`):

- **Cold tree** (first native build after clone): often **minutes** — setuptools, CMake, and extensions compile from scratch.
- **Warm tree, small change**: often **tens of seconds** — `setup.py` starts; incremental logic may skip up-to-date extensions (`DD_CMAKE_INCREMENTAL_BUILD`, mtime checks).
- **Warm tree, comment-only header**: may still invoke `build_ext`, but most extensions log `skipping … (up-to-date)`.

One staged `profiler_state.hpp` comment still launches the full `build_ext` driver when blocking mode is on; incremental skips limit actual recompilation.

Uses `.venv/bin/python` when present. On stale `.eggs` errors (`Directory not empty` / errno 66), removes `.eggs` and retries once.

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

**On commit (reminder by default):**
```bash
hooks/autohook.sh install   # once per clone
git commit                  # prints rebuild reminder if product native files are staged
```

**On commit (blocking rebuild, opt-in):**
```bash
export DD_BUILD_NATIVE_ON_COMMIT=1
git commit                  # runs build_ext --inplace; aborts commit if build fails
```

**Quick Rebuild (Recommended):**
```bash
pip install -e .
```
Rebuilds changed native extensions. Fast and usually sufficient.

**Manual in-place rebuild (same command as blocking pre-commit mode):**
```bash
python setup.py build_ext --inplace
```

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
│   └── check-native-changes # Detects native code and dependency changes
├── post-checkout/           # Post-checkout hooks
│   └── check-native-changes # Detects native code and dependency changes
└── scripts/                 # Shared scripts
    ├── build-native-ext.sh  # Pre-commit native warn / optional rebuild
    └── check-native-changes # Native change and dependency detection logic
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
