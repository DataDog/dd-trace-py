---
name: run-tests
description: Select and run relevant validation through scripts/run-tests, including environment discovery and rebuilds.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - TodoWrite
---

# Test validation

Use `scripts/run-tests` from the repository root; never invoke pytest directly.
The [testing guide](../../../docs/contributing-testing.rst) owns environment setup,
fixtures, snapshots, and suitespec configuration.

## Discover and select

```bash
scripts/run-tests --list <changed-files>
```

Use the returned environment hashes, Python versions, and dependencies. Do not copy hashes
from old runs or invent environment names. Status messages go to stderr; when parsing JSON,
keep stderr separate from stdout. `--all-suites --list` discovers suites beyond changed-file matches.

Start with a focused environment during iteration. Expand based on affected behavior:

- Integration changes: exercise the changed operation, sync/async or streaming paths as applicable,
  and dependency-version boundaries affected by the change.
- Core tracing, propagation, configuration, or patching changes: include representative consuming
  integrations when their behavior can change, even if no contrib file was edited.
- Python compatibility: select the affected versions from the supported matrix, not automatically
  the newest interpreter. Native changes may require multiple interpreter or platform variants.
- Test infrastructure: exercise the changed runner/service/selection path; a passing unrelated
  smoke test does not validate every infrastructure change.

Suite discovery is a starting point, not proof of coverage. Explain any important gaps.

## Execute

```bash
scripts/run-tests --venv <hash>
scripts/run-tests --venv <hash> -- -vv -k <test-name>
scripts/run-tests --venv <hash1> --venv <hash2>
```

One `--` separates runner arguments from test-command arguments. The runner manages required
services and reuses matching environments. Check Docker/service prerequisites in the testing guide.

```bash
scripts/run-tests -r --venv <hash>
```

Use `-r` after native source or build-configuration changes, or after updating from main when
those inputs changed. Dependency locks and environment definitions invalidate caches automatically,
but not every source/build input does. The legacy runner `-s` is a no-op; test-command `-s`
after `--` still disables output capture.

## Diagnose and report

Read the actual failure before rerunning. Distinguish setup errors from behavioral failures,
fix the relevant cause, and rerun the affected test. Ask only for information the repository,
logs, and supplied context cannot resolve. Report environments and tests run, outcomes, and gaps.

For resource-constrained reproductions, `DD_TEST_CPUS` and `DD_TEST_MEMORY` configure the Compose
testrunner in `docker-compose.base.yml`. Verify that the run uses that container and inspect its
limits; setting these variables alone does not constrain an arbitrary local process.
