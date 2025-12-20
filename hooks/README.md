# Git Hooks

This project uses [Autohook](https://github.com/Autohook/Autohook) to manage git hooks.

## Installation

To install all git hooks, run:

```bash
hooks/autohook.sh install
```

This will create symlinks in `.git/hooks/` for all configured hook types.

## Available Hooks

### pre-commit
Runs before each commit. Contains:
- Code formatting and linting checks
- Other pre-commit validations

### post-merge
Runs after `git pull` or `git merge`. Contains:
- **check-native-changes**: Detects changes to native code files (C, C++, Rust, Cython) and alerts you to rebuild

### post-checkout
Runs after `git checkout` or `git switch`. Contains:
- **check-native-changes**: Detects changes to native code files and alerts you to rebuild

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

You need to rebuild native extensions!

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
│   └── ...
├── post-merge/              # Post-merge hooks
│   └── check-native-changes # Detects native code changes
├── post-checkout/           # Post-checkout hooks
│   └── check-native-changes # Detects native code changes
└── scripts/                 # Shared scripts
    └── check-native-changes # Native change detection logic
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
