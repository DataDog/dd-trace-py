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
If no suite matches, verify repository-relative paths and inspect the relevant `suitespec.yml`
patterns (including nested files under `tests/`). The runner falls back to listing all suites;
that fallback is not evidence that those suites cover the changed file. Use `--all-suites --list`
to find an appropriate environment and address missing mappings when adding tests.

## Execute

```bash
scripts/run-tests --venv <hash>
scripts/run-tests --venv <hash> -- -vv -k <test-name>
scripts/run-tests --venv <hash1> --venv <hash2>
```

One `--` separates runner arguments from test-command arguments. The runner manages required
services and reuses matching environments. Check Docker/service prerequisites in the testing guide.
`--venv` resolves hashes across all available suites and bypasses changed-file discovery, so
unrelated local edits do not restrict a direct-hash rerun. An unknown hash fails; rediscover it
rather than guessing an environment name.

```bash
scripts/run-tests -r --venv <hash>
```

Use `-r` after native source or build-configuration changes, or after updating from main when
those inputs changed. Dependency locks and environment definitions invalidate caches automatically,
but not every source/build input does. The runner's `-s` option is a no-op; test-command `-s`
after `--` still disables output capture.

## Diagnose and report

Read the actual failure before rerunning. Distinguish setup errors from behavioral failures,
fix the relevant cause, and rerun the affected test. Ask only for information the repository,
logs, and supplied context cannot resolve. Report environments and tests run, outcomes, and gaps.

For resource-constrained reproductions, `DD_TEST_CPUS` and `DD_TEST_MEMORY` configure the Compose
testrunner in `docker-compose.base.yml`. Verify that the run uses that container and inspect its
limits; setting these variables alone does not constrain an arbitrary local process.

```bash
DD_TEST_CPUS=0.5 DD_TEST_MEMORY=1g scripts/run-tests --venv <hash> -- -vv -k <test-name>
DD_TEST_CPUS=0.5 DD_TEST_MEMORY=1g docker compose -f docker-compose.yml config --format json \
  | jq '.services.testrunner | {cpus, mem_limit}'
```

These values are examples, not a standard CI machine profile. For Podman compatibility, inspect
the same configuration the runner uses: replace `-f docker-compose.yml` with
`-f docker-compose.base.yml -f docker-compose.podman.yml`. While the test runs, identify its
testrunner container for the current checkout and verify applied limits and usage:

```bash
docker ps --filter label=com.docker.compose.service=testrunner
docker inspect --format 'NanoCpus={{.HostConfig.NanoCpus}} Memory={{.HostConfig.Memory}}' <container-id>
docker stats --no-stream <container-id>
```

For intermittent failures, repeat a focused run and record the seed. Use `--randomly-seed` only
when the selected environment includes `pytest-randomly`; otherwise omit that option for a
plain repeated run. This example stops on the first failure and preserves its exit status:

```bash
(
  for test_attempt in 1 2 3 4 5; do
    test_seed=$RANDOM
    printf 'Attempt %s, seed %s\n' "$test_attempt" "$test_seed"
    DD_TEST_CPUS=0.5 DD_TEST_MEMORY=1g scripts/run-tests --venv <hash> \
      -- -vv -k <test-name> --randomly-seed="$test_seed" || exit "$?"
  done
)
```

Replay a failure with the recorded integer seed and the same environment, test selection, and
resource limits. Include those details in the report; repeated passes do not establish that a
timing or ordering bug is fixed.
