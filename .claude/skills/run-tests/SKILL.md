---
name: run-tests
description: >
  Validate code changes by intelligently selecting and running the appropriate test suites.
  Use this when editing code to verify changes work correctly, run tests, validate functionality,
  or check for regressions. Automatically discovers affected test suites, selects the minimal
  set of venvs needed for validation, and handles test execution with Docker services as needed.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - TodoWrite
---

# Test Suite Validation Skill

This skill helps you efficiently validate code changes by running the appropriate subset of the test suite. It uses `scripts/run-tests` to intelligently discover affected tests and run only what's necessary for validation.

## When to Use This Skill

Use this skill when you have:
- Made changes to source code files and want to validate they work
- Fixed a bug and want to verify the fix
- Added a feature and need test coverage
- Modified test infrastructure or configuration
- Want to verify changes don't break existing functionality

## How This Skill Works

### Step 1: Identify Changed Files

First, determine which files were modified:
- If you have pending edits, I'll identify the changed files from the current session
- I'll look at git status to find staged, unstaged, and untracked changes
- You can also specify files explicitly if working on specific changes

### Step 2: Discover Available Test Suites

I'll use the `scripts/run-tests` script to discover what test suites match your changes:

```bash
scripts/run-tests --list <edited-files>
```

This outputs JSON showing:
- Available test suites that match your changed files
- All venvs (Python versions + package combinations) available for each suite
- Their hashes, Python versions, and package versions

### Step 3: Intelligently Select Venvs

Rather than running ALL available venvs (which could take hours), I'll select the **minimal set** needed to validate your changes:

#### For Core/Tracing Changes (Broad Impact)
When you modify files like:
- `ddtrace/internal/core/*`, `ddtrace/_trace/*`, `ddtrace/trace/*`
- `ddtrace/_monkey.py`, `ddtrace/settings/*`
- `ddtrace/constants.py`

**Strategy:** Run core tracer + internal tests with **1 venv each**
- Example: `tracer` suite with latest Python + `internal` suite with latest Python
- This validates broad-reaching changes without excessive overhead
- Skip integration suites unless the change directly affects integration code

#### For Integration/Contrib Changes (Targeted Impact)
When you modify files like:
- `ddtrace/contrib/flask/*`, `ddtrace/contrib/django/*`, etc.
- `ddtrace/contrib/*/patch.py` or integration-specific code

**Strategy:** Run ONLY the affected integration suite with **1-2 venvs**
- Example: Flask changes → run `contrib::flask` suite with latest Python
- If change involves multiple versions (e.g., Django 3.x and 4.x), pick 1 venv per major version
- Skip unrelated integrations

#### For Test-Only Changes
When you modify `tests/` files (but not test infrastructure):
- Run only the specific test files/functions modified
- Use pytest args: `-- -k test_name` or direct test file paths

#### For Test Infrastructure Changes
When you modify:
- `tests/conftest.py`, `tests/suitespec.yml`, `scripts/run-tests`, `riotfile.py`

**Strategy:** Run a quick smoke test suite
- Example: `internal` suite with 1 venv as a sanity check
- Or run small existing test suites to verify harness changes

### Step 4: Execute Selected Venvs

I'll run the selected venvs using:

```bash
scripts/run-tests --venv <hash1> --venv <hash2> ...
```

This will:
- Start required Docker services (redis, postgres, etc.)
- Run tests in the specified venvs sequentially
- Stop services after completion
- Show real-time output and status

### Step 5: Handle Results

**If tests pass:** ✅ Your changes are validated!

**If tests fail:** 🔴 I'll:
- Show you the failure details
- Identify which venv failed
- Ask clarifying questions to understand the issue
- Offer to run specific failing tests with more verbosity
- Help iterate on fixes and re-run

For re-running specific tests:
```bash
scripts/run-tests --venv <hash> -- -vv -k test_name
```

## Venv Selection Strategy in Detail

### Understanding Venv Hashes

From `scripts/run-tests --list`, you'll see output like:

```json
{
  "suites": [
    {
      "name": "tracer",
      "venvs": [
        {
          "hash": "abc123",
          "python_version": "3.8",
          "packages": "..."
        },
        {
          "hash": "def456",
          "python_version": "3.11",
          "packages": "..."
        }
      ]
    }
  ]
}
```

### Selection Rules

1. **Latest Python version is your default choice**
   - Unless your change specifically targets an older Python version
   - Example: if fixing Python 3.8 compatibility, also test 3.8

2. **One venv per suite is usually enough for iteration**
   - Only run multiple venvs per suite if:
     - Change impacts multiple Python versions differently
     - Testing package compatibility variations (e.g., Django 3.x vs 4.x)
     - Initial validation passed and you want broader coverage

3. **Minimize total venvs**
   - 1-2 venvs total for small targeted changes
   - 3-4 venvs maximum for broader changes
   - Never run 10+ venvs for initial validation (save that for CI)

4. **Consider test runtime**
   - Each venv can take 5-30 minutes depending on suite
   - With 2 venvs you're looking at 10-60 minutes for iteration
   - With 5 venvs you're looking at 25-150 minutes
   - Scale appropriately for your patience and deadline

### Using `--venv` Directly

When you have a specific venv hash you want to run, you can use it directly without specifying file paths:

```bash
scripts/run-tests --venv e06abee
```

The `--venv` flag automatically searches **all available venvs** across all suites, so it works regardless of what files you have locally changed. This is useful when:
- You know exactly which venv you want to test
- You have unrelated local changes that would otherwise limit suite matching
- You want to quickly re-run a specific venv without file path arguments

## Examples

### Example 1: Fixing a Flask Integration Bug

**Changed file:** `ddtrace/contrib/internal/flask/patch.py`

```bash
scripts/run-tests --list ddtrace/contrib/internal/flask/patch.py
# Output shows: contrib::flask suite available

# Select output (latest Python):
# Suite: contrib::flask
# Venv: hash=e06abee, Python 3.13, flask

# Run with --venv directly (searches all venvs automatically)
scripts/run-tests --venv e06abee
# Runs just Flask integration tests
```

### Example 2: Fixing a Core Tracing Issue

**Changed file:** `ddtrace/_trace/tracer.py`

```bash
scripts/run-tests --list ddtrace/_trace/tracer.py
# Output shows: tracer suite, internal suite available

# Select strategy:
# - tracer: latest Python (e.g., abc123)
# - internal: latest Python (e.g., def456)

# Run with --venv directly (searches all venvs automatically)
scripts/run-tests --venv abc123 --venv def456
# Validates core tracer and internal components
```

### Example 3: Fixing a Test-Specific Bug

**Changed file:** `tests/contrib/flask/test_views.py`

```bash
scripts/run-tests --list tests/contrib/flask/test_views.py
# Output shows: contrib::flask suite

# Run just the specific test:
scripts/run-tests --venv flask_py311 -- -vv tests/contrib/flask/test_views.py
```

### Example 4: Iterating on a Failing Test

First run shows one test failing:

```bash
scripts/run-tests --venv flask_py311 -- -vv -k test_view_called_twice
# Focused on the specific failing test with verbose output
```

## Best Practices

### DO ✅

- **Start small**: Run 1 venv first, expand only if needed
- **Be specific**: Use pytest `-k` filter when re-running failures
- **Check git**: Verify you're testing the right files with `git status`
- **Read errors**: Take time to understand test failures before re-running
- **Ask for help**: When unclear what tests to run, ask me to analyze the changes

### DON'T ❌

- **Run all venvs initially**: That's what CI is for
- **Skip the minimal set guidance**: It's designed to save you time
- **Ignore service requirements**: Some suites need Docker services up
- **Run tests without changes saved**: Make sure edits are saved first
- **Iterate blindly**: Understand what's failing before re-running

## Troubleshooting

### Docker services won't start
```bash
# Manually check/stop services:
docker-compose ps
docker-compose down
```

### Can't find matching suites
- Verify the file path is correct
- Check `tests/suitespec.yml` to understand suite patterns
- Your file might not be covered by any suite pattern yet

### Test takes too long
- You may have selected too many venvs
- Try running with just 1 venv
- Use pytest `-k` to run subset of tests

## Technical Details

### Architecture

The `scripts/run-tests` system:
- Maps source files to test suites using patterns in `tests/suitespec.yml`
- Uses `riot` to manage multiple Python/package combinations as venvs
- Each venv is a self-contained environment
- Docker services are managed per suite lifecycle
- Tests can pass optional pytest arguments with `--`

### Supported Suite Types

Primary suites for validation:
- `tracer`: Core tracing functionality tests
- `internal`: Internal component tests
- `contrib::*`: Integration with specific libraries (flask, django, etc.)
- `integration_*`: Cross-library integration scenarios
- Specialized: `telemetry`, `profiling`, `appsec`, `llmobs`, etc.

### Environment Variables

Some suites require environment setup:
- `DD_TRACE_AGENT_URL`: For snapshot-based tests
- Service-specific variables for Docker containers
- These are handled automatically by the script
