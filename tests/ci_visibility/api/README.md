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
 
1. Set up (and activate) an environment (eg: using `pip install ddtrace` or `riot shell`)
1. Run the script:
   1. Set expected environment variables (eg: `DD_API_KEY` and `DD_CIVISIBILITY_AGENTLESS_ENABLED`)
   1. Run the script, eg: `python tests/ci_visibility/api/fake_runner_all_pass.py`

#### As tests

1. List the environments with `scripts/run-tests --list tests/ci_visibility/api`.
1. Select a hash from the `ci_visibility::ci_visibility:snapshot` suite; the test runner starts `testagent`:
   1. All tests: `scripts/run-tests --venv <environment-hash> -- -k FakeApiRunnersSnapshotTestCase`
   1. Individual test: `scripts/run-tests --venv <environment-hash> -- -k test_manual_api_fake_runner_mix_fail_itr_test_level`
