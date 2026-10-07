# CI / Test Visibility API tests

These tests exercise the CI / Test Visibility API.

## Fake runners snapshot tests

The fake runners are standalone scripts that simulate manual usage of the API
(either through the external manual API or through the extended internal API).

### Mocking

Since the fake runners are standalone and execute as snapshot tests in their own process, they do some of their own mocking.

### Updating snapshot tests

Refer to the `ddtrace` contributor documentation for how to update snapshot
tests.

### Running fake runners

#### Manually
 
1. Set up and activate an environment with `ddtrace` installed.
1. Run the script:
   1. Set expected environment variables (eg: `DD_API_KEY` and `DD_CIVISIBILITY_AGENTLESS_ENABLED`)
   1. Run the script, eg: `python tests/ci_visibility/api/fake_runner_all_pass.py`

#### As tests

1. List the environments with `scripts/run-tests --list tests/ci_visibility/api`.
1. Run the selected environment; the test runner starts `testagent` when needed:
   1. All tests: `scripts/run-tests --venv <environment-hash> -- -k FakeApiRunnersSnapshotTestCase`
   1. Individual test: `scripts/run-tests --venv <environment-hash> -- -k test_manual_api_fake_runner_mix_fail_itr_test_level`

### Suite Test Impact Analysis reporting

When Test Impact Analysis is enabled, every completed suite reports
`test.itr.tests_skipping.count` as a non-negative integer-valued metric and
`_dd.ci.itr.tests_skipped` as the string `"true"` exactly when that count is positive.
Empty suites and suites without Test Impact Analysis skips report zero and `"false"`,
including when test skipping or coverage collection is disabled. Both fields are
omitted when Test Impact Analysis is disabled or tests run in pytest-xdist workers.
This applies to both pytest implementations and all xdist scheduling modes:
workers cannot reliably determine the complete suite's results. Existing xdist
test skipping, session totals, and suite event timing are preserved.

Test mode counts the suite's Test Impact Analysis skipped test executions; suite
mode counts a skipped suite once. Framework skips, disabled tests, and forced runs
do not contribute. A collected suite is marked as skipped by Test Impact Analysis
only after every selected child has finished with a Test Impact Analysis skip
and a final skip outcome. Pytest waits for all selected tests even when a suite's
items are interleaved with other suites. After early termination, started suites
and modules are finalized without treating incomplete suites as fully skipped by
Test Impact Analysis. Counters belong to individual suites and propagate to
ancestors using the existing session counting rules.
