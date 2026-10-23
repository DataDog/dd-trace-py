# Writing tests

Follow the repository's run-tests and lint skills. Snapshot conventions are in
[the testing guide](../docs/contributing-testing.rst), under “Snapshot tests”.

- Test observable behavior and configuration through environment variables.
- Use subprocess tests for global singleton, startup, and shutdown behavior; set
  the environment before importing ddtrace and minimize mocking.
- Use the snapshot marker or context fixture. Baseline names are stable;
  `snapshot.token` identifies one execution and must be propagated to producers.
- Finish and join every producer before verification. Trace-count polling does
  not prove that all spans have arrived, and sleeps do not establish isolation.
- Snapshot mismatches must fail. Do not add automatic xfails, broad ignores, or
  retries to conceal them. Known product defects need explicit strict xfails.
- Prefer public behavior tests over tests of internal methods. Focused tests are
  appropriate for complex core logic and controlled transport failures.
