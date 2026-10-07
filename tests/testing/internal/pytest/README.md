# Suite Test Impact Analysis reporting

The `ddtrace.testing.internal.pytest` plugin reports whether Test Impact Analysis
has affected a suite's duration, so affected durations can be excluded from
comparisons. Any actual TIA skip makes `_dd.ci.itr.tests_skipped` `"true"`,
regardless of the suite's overall status or other tests' outcomes.

With TIA enabled, completed suites also report `test.itr.tests_skipping.count`:
test mode counts TIA-skipped executions; suite mode reports one for an affected
suite. Suites without TIA skips report zero/false, including when skipping or
coverage collection is disabled. Both fields are omitted when TIA is disabled.

Both fields are omitted for pytest-xdist workers in every scheduling mode,
because worker assignments do not provide a complete view of a suite. Existing
xdist test skipping, session totals, and suite event timing are preserved.

Serial pytest waits for all selected executions before emitting a suite,
including interleaved selections, and finalizes started parents after early
termination. Framework skips, disabled tests, and forced runs do not themselves
contribute to the count, and do not cancel an actual TIA skip in the same suite.

This feature is limited to the current pytest plugin. It does not add suite
reporting to unittest, the legacy `_plugin_v2` plugin, or the manual CI Visibility
API.
