"""The coverage.py side of Test Optimization.

Test Optimization reuses a coverage.py session to report the code coverage percentage and to upload LCOV reports.
This package holds the logic and the state of that: the coverage instance, report generation, LCOV report
conversion and the helpers that tell whether pytest-cov or `coverage run` owns the session. It is unrelated to
the line coverage collector in `ddtrace.internal.coverage`.

The coverage.py integration (`ddtrace.contrib.internal.coverage`) only patches `Coverage.report` on top of this
package. Nothing here imports `ddtrace.contrib`, so loading it does not load the tracer.
"""
