File-level coverage benchmark
=============================

This scenario measures Datadog's Python 3.12+ ``sys.monitoring`` file-level
coverage collector over a generated multi-module corpus. Each timed sample
starts a fresh Python process, instruments and imports the corpus, and runs
many test-like coverage contexts. The ``baseline`` configuration runs the same
corpus without coverage to expose subprocess or host noise.

Run it against ``main`` with Python 3.12::

  PYTHON_VERSION=3.12.8 scripts/run-benchmarks \
    --scenario coverage_file_level \
    --baseline Datadog/dd-trace-py@main \
    --candidate . \
    --artifacts ./benchmark-artifacts/

Then inspect the comparison::

  scripts/perf-analyze ./benchmark-artifacts/

The explicit Python version is required because the general microbenchmark
image defaults to Python 3.9, which does not exercise the ``sys.monitoring``
implementation.
