Coverage monitoring benchmark
=============================

This scenario measures Datadog's Python 3.12+ ``sys.monitoring`` coverage
collector over a generated multi-module corpus. Each timed sample starts a
fresh Python process, instruments and imports the corpus, and runs many
test-like coverage contexts.

The configurations are:

* ``baseline``: runs the same corpus without coverage to expose subprocess and
  host noise;
* ``line_level``: sets ``_DD_COVERAGE_FILE_LEVEL=false`` and collects coverage
  through ``LINE`` events;
* ``file_level``: sets ``_DD_COVERAGE_FILE_LEVEL=true`` and collects coverage
  through ``PY_START`` events.

The microbenchmark pipeline runs on Python 3.12. To reproduce it locally::

  scripts/run-benchmarks \
    --scenario coverage_monitoring \
    --baseline Datadog/dd-trace-py@main \
    --candidate . \
    --artifacts ./benchmark-artifacts/

Then inspect the comparison::

  scripts/perf-analyze ./benchmark-artifacts/
