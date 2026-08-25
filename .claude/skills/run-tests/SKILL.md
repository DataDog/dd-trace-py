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

## Key Principles

1. **Always use the run-tests skill** when testing code changes - it's optimized for intelligent suite discovery
2. **Never run pytest directly** - use `scripts/run-tests`
3. **Use `-s` only for reruns** - first establish a current ddtrace installation, then reuse it
4. **Minimal venvs for iteration** - run 1-2 venvs initially, expand only if needed
5. **Use `--dry-run` first** - see what would run before executing
6. **Follow official docs** - `docs/contributing-testing.rst` is the source of truth for testing procedures

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
- Pass pytest arguments after one separator, for example `-- -k test_name`

#### For Test Infrastructure Changes
When you modify:
- `tests/conftest.py`, suitespec files, `scripts/run-tests`, or `.uv` locks

**Strategy:** Run a quick smoke test suite
- Example: `internal` suite with 1 venv as a sanity check
- Or run small existing test suites to verify harness changes

### Step 4: Execute Selected Environments

Run the selected environment hashes. The runner creates the uv environment, installs dd-trace-py, installs its exact lock, and manages required services:

```bash
scripts/run-tests --venv <environment-hash-1> --venv <environment-hash-2>
```

After a successful first run, reuse the verified ddtrace installation for faster iteration:

```bash
scripts/run-tests -s --venv <environment-hash-1> --venv <environment-hash-2>
```

Use `-s` before the `--` separator. A `-s` after the separator is pytest's output-capture option. Omit the runner flag
after native-code or project-metadata changes, or after updating the branch from main.

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
scripts/run-tests -s --venv <environment-hash> -- -vv -k test_name
```

## When Tests Fail

When you encounter test failures, follow this systematic approach:

1. **Review the failure details carefully** - Don't just skim the error, understand what's actually failing
2. **Understand what's failing** - Don't blindly re-run; analyze the root cause
3. **Make code changes** - Fix the underlying issue
4. **Re-run with more verbosity if needed** - Use `-vv` or `-vvv` for detailed output
5. **Iterate until tests pass** - Repeat the process with each fix

## Venv Selection Strategy in Detail

### Understanding Environment Hashes

From `scripts/run-tests --list`, you'll see output like:

```json
{
  "suites": [
    {
      "name": "tracer",
      "venvs": [
        {
          "hash": "0123456789ab",
          "python_version": "3.9",
          "packages": "..."
        },
        {
          "hash": "abcdef012345",
          "python_version": "3.14",
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
   - Example: if fixing Python 3.9 compatibility, also test 3.9

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

When you have a specific environment hash, run it directly without specifying file paths:

```bash
scripts/run-tests --venv <environment-hash>

# Subsequent run while iterating on Python code
scripts/run-tests -s --venv <environment-hash>
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
# Select a Python 3.13 hash for contrib::flask from the JSON output.
scripts/run-tests --venv <environment-hash>
```

### Example 2: Fixing a Core Tracing Issue

**Changed file:** `ddtrace/_trace/tracer.py`

```bash
scripts/run-tests --list ddtrace/_trace/tracer.py
# Output shows: tracer suite, internal suite available

# Select strategy:
# - tracer: latest Python (e.g., abc123)
# - internal: latest Python (e.g., def456)

scripts/run-tests --venv <tracer-hash> --venv <internal-hash>
```

### Example 3: Fixing a Test-Specific Bug

**Changed file:** `tests/contrib/flask/test_views.py`

```bash
scripts/run-tests --list tests/contrib/flask/test_views.py
# Output shows: contrib::flask suite

scripts/run-tests --venv <environment-hash> -- -vv tests/contrib/flask/test_views.py
```

### Example 4: Iterating on a Failing Test

After the first run shows a test failing, narrow the pytest selection:

```bash
scripts/run-tests -s --venv <environment-hash> -- -vv -k test_view_called_twice
# Focused on the specific failing test with verbose output
```

## Best Practices

### DO ✅

- **Use `-s` for current environments**: Skip reinstalling ddtrace during ordinary Python-only iteration
- **Start small**: Run 1 venv first, expand only if needed
- **Be specific**: Use pytest `-k` filter when re-running failures
- **Check git**: Verify you're testing the right files with `git status`
- **Read errors**: Take time to understand test failures before re-running
- **Ask for help**: When unclear what tests to run, ask me to analyze the changes

### DON'T ❌

- **Use `-s` after native or metadata changes**: Refresh the editable installation first
- **Run all venvs initially**: That's what CI is for
- **Skip the minimal set guidance**: It's designed to save you time
- **Ignore service requirements**: Some suites need Docker services up
- **Run tests without changes saved**: Make sure edits are saved first
- **Iterate blindly**: Understand what's failing before re-running

## Additional Testing Resources

**For comprehensive testing guidance, refer to the contributing documentation:**

- **[docs/contributing-testing.rst](../../docs/contributing-testing.rst)** - Detailed testing guidelines
  - What kind of tests to write (unit tests, integration tests, e2e tests)
  - When to write tests (feature development, bug fixes)
  - Where to put tests in the repository
  - Prerequisites (Docker, uv)
  - Complete `scripts/run-tests` usage examples
  - uv environment and lock management details
  - Running specific test files and functions
  - Test debugging strategies

- **[docs/contributing.rst](../../docs/contributing.rst)** - PR and testing requirements
  - All changes need tests or documented testing strategy
  - How tests fit into the PR review process
  - Testing expectations for different types of changes

- **[docs/contributing-design.rst](../../docs/contributing-design.rst)** - Test architecture context
  - How products, integrations, and core interact
  - Where different types of tests should live
  - Testing patterns for each library component

**When to reference these docs:**
- First time writing tests for this project → Read `contributing-testing.rst`
- Understanding test requirements for PRs → Read `contributing.rst`
- Need context on test architecture → Read `contributing-design.rst`

## Troubleshooting

### Docker services won't start
```bash
# Manually check/stop services:
docker compose ps
docker compose down
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
- Expands suitespec matrices into uv environments with committed locks
- Each venv is a self-contained environment
- Docker services are managed per suite lifecycle
- `-s` explicitly enables reuse of a verified ddtrace installation
- Pass test-command arguments after one `--` separator.

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

#### Resource Limiting (for testing under constrained environments)

You can limit CPU and memory resources to simulate resource-constrained CI environments where multiple jobs run in parallel. This helps reproduce flaky tests that fail due to timing issues, race conditions, or resource exhaustion.

**Environment Variables:**
- `DD_TEST_CPUS`: CPU limit (e.g., `0.25`, `0.5`, `1.0`, `2.0`)
- `DD_TEST_MEMORY`: Memory limit with unit (e.g., `512m`, `1g`, `2g`)

**Usage:**
```bash
# Run tests with resource constraints
DD_TEST_CPUS=0.5 DD_TEST_MEMORY=1g scripts/run-tests --venv <environment-hash>

# Run specific test file with heavy constraints
DD_TEST_CPUS=0.25 DD_TEST_MEMORY=1g scripts/run-tests tests/path/to/test.py

# Multiple runs to catch intermittent failures
for i in {1..10}; do
  DD_TEST_CPUS=0.5 DD_TEST_MEMORY=1g scripts/run-tests --venv <environment-hash> -- --randomly-seed=$RANDOM
done
```

**Recommended Resource Limits:**

- **Moderate Load (Typical Shared CI):**
  ```bash
  DD_TEST_CPUS=2.0 DD_TEST_MEMORY=4g
  ```
  Simulates a CI runner with some other jobs running. Good for initial testing.

- **Heavy Load (Busy CI Server):**
  ```bash
  DD_TEST_CPUS=1.0 DD_TEST_MEMORY=2g
  ```
  Simulates a heavily loaded CI server with many concurrent jobs. **Recommended starting point** for reproducing flaky tests.

- **Extreme Load (Stress Testing):**
  ```bash
  DD_TEST_CPUS=0.5 DD_TEST_MEMORY=1g
  ```
  Simulates extreme resource contention. Good for surfacing timing issues and race conditions.

- **Critical Failure Conditions:**
  ```bash
  DD_TEST_CPUS=0.25 DD_TEST_MEMORY=512m
  ```
  Forces maximum resource pressure. Use this to find the breaking point or reproduce worst-case scenarios.

**When to Use Resource Limits:**

1. **Investigating flaky tests** - Tests that pass locally but fail in CI
2. **Timing-sensitive tests** - Tests involving async operations, network calls, or multiprocessing
3. **Resource exhaustion issues** - Tests that fail under memory pressure or CPU throttling
4. **Race condition detection** - Slower execution can expose timing bugs
5. **Reproducing CI failures** - When you have a seed that failed in CI

**Verifying Limits Are Applied:**
```bash
# Check configuration before running
DD_TEST_CPUS=0.5 DD_TEST_MEMORY=1g docker compose config | grep -A 5 testrunner

# Monitor actual resource usage during test run (in another terminal)
docker stats
```

**Example: Testing a Flaky Test**
```bash
# Run a known flaky test 20 times with resource constraints
DD_TEST_CPUS=0.5 DD_TEST_MEMORY=1g scripts/run-tests \
  tests/appsec/integrations/flask_tests/test_iast_flask_testagent.py::test_iast_unvalidated_redirect \
  -- --count=20
```
