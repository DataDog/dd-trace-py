"""
The exploration debugger is a special case of the debugger that is used to
instrument all the lines and functions within a codebase during the test runs.
This is to ensure that the tests still pass with the instrumentation on.

To run an exploration test, set the environment variable

    PYTHONPATH=/path/to/tests/debugging/exploration/

Line and function instrumentation can be turned off independently by setting
the environment variables ``DD_DEBUGGER_EXPL_COVERAGE_ENABLED`` and
``DD_DEBUGGER_EXPL_PROFILER_ENABLED`` to ``0``. As a proof of work for the
instrumentation, we use line probes to measure line coverage during tests.
For function probes, we count the number of times a function is called, like
a deterministic profiler. Aa the end of the test runs, we report the function
calls and the line coverage.

Encoding can be turned off for very large test suites by setting the environment
variable ``DD_DEBUGGER_EXPL_ENCODE`` to ``0``.
"""
