Benchmarks
----------

These benchmarks are intended to provide stable and reproducible measurements of the performance characteristics of the ``ddtrace`` library. A scenario is defined using a simple Python framework. Docker is used to build images for the execution of scenarios against different versions of ``ddtrace``.

.. _developer_workflow:

Developer Workflow
^^^^^^^^^^^^^^^^^^

Use ``scripts/run-benchmarks`` and ``scripts/perf-analyze`` for day-to-day benchmark work. These wrap the lower-level ``scripts/perf-run-scenario`` with scenario discovery, a consistent run ID, and result analysis.

**Step 1 — Discover relevant scenarios**

Find which benchmark suites are affected by your changes::

  # Based on your git changes
  scripts/run-benchmarks --list

  # For specific files
  scripts/run-benchmarks --list ddtrace/_trace/span.py

  # All available suites
  scripts/run-benchmarks --list --all-suites

Output is JSON with scenario names, matched files, and available config variants.

**Step 2 — Run scenarios**

::

  # Run a single scenario (latest PyPI vs local)
  scripts/run-benchmarks --scenario span --artifacts ./benchmark-artifacts/

  # Run multiple scenarios under the same run ID
  scripts/run-benchmarks --scenario span --scenario tracer --artifacts ./benchmark-artifacts/

  # Faster iteration: run only specific config variants
  scripts/run-benchmarks --scenario span --configs start,start-finish --artifacts ./benchmark-artifacts/

  # Compare two specific PyPI versions
  scripts/run-benchmarks --scenario span --baseline ddtrace==4.5.0 --candidate ddtrace==4.6.0 --artifacts ./benchmark-artifacts/

  # Add another scenario to an existing run (reuse the run ID printed above)
  scripts/run-benchmarks --scenario http_propagation_extract --run-id <run-id> --artifacts ./benchmark-artifacts/

  # Dry-run to preview what would execute
  scripts/run-benchmarks --dry-run --scenario span

A run ID is printed at the start of each invocation. Multiple ``--scenario`` flags share the same run ID, so all results land in one artifact directory. Use ``--run-id`` to append scenarios to a previous run.

Note: ``--configs`` applies the same filter to every scenario. When running scenarios with different config naming, run them in separate invocations.

**Step 3 — Analyze results**

::

  # Human-readable summary (auto-finds latest run)
  scripts/perf-analyze benchmark-artifacts/

  # Analyze a specific run
  scripts/perf-analyze benchmark-artifacts/<run-id>/

  # Markdown table for PR comments
  scripts/perf-analyze benchmark-artifacts/ --markdown

  # JSON for programmatic use
  scripts/perf-analyze benchmark-artifacts/ --json

The summary shows mean ± ``stddev``, change %, ratio, and a significance label for each config. Changes under 2% are reported as not significant. The ``--markdown`` output is suitable for pasting directly into a pull request description.

**Step 4 — Profiling (optional)**

When the summary shows a regression and you need to know *why*::

  # Collect viztracer profiling data (generates ~700MB per config)
  scripts/run-benchmarks --scenario span --configs start-finish --profile --artifacts ./benchmark-artifacts/

  # Show top functions by exclusive time (ddtrace paths only)
  scripts/perf-analyze benchmark-artifacts/ --profile-top 20 --filter ddtrace --min-calls 1000

  # Compare baseline vs candidate function-by-function
  scripts/perf-analyze benchmark-artifacts/ --profile-compare --filter ddtrace --min-calls 1000

Use ``--configs`` to limit profiling to one config variant — ``viztracer`` files are large and slow to process.

.. _framework:

Framework
^^^^^^^^^

A scenario requires:

* ``scenario.py``: implements a class for running a benchmark
* ``config.yaml``: specifies one or more sets of configuration variables for the benchmark
* ``requirements_scenario.txt``: any additional dependencies

The scenario class inherits from ``bm.Scenario`` and includes the configurable variables. The execution of the benchmark uses the ``run()`` generator function to yield a function that will handle the execution of a specified number of loops.

Remember that the ``name: str`` attribute is inherited from ``bm.Scenario``, and keep in mind we use ``dataclasses`` underneath now instead of ``attrs``.

Example
~~~~~~~

``scenario.py``
+++++++++++++++

::

  import bm


  class MyScenario(bm.Scenario):
      size: int

      def run(self):
          size = self.size

          def bm(loops):
              for _ in range(loops):
                  2 ** size

          yield bm


``config.yaml``
+++++++++++++++

::

  small-size:
    size: 10
  large-size:
    size: 1000
  huge-size:
    size: 1000000


.. _run:

Advanced: direct invocation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

For direct control over the Docker execution, ``scripts/perf-run-scenario`` can be used without the ``run-benchmarks`` wrapper. This is useful for scripting or CI.

The scenario can be run using the built image to compare two versions of the library and save the results in a local artifacts folder::

  scripts/perf-run-scenario <scenario> <baseline-version> <candidate-version> --artifacts <artifacts>

The version specifiers can reference published versions on PyPI, git repositories, or `.` for your local version.

To run benchmarks against a single version without comparison, pass an empty string ``""`` as the second version::

  scripts/perf-run-scenario <scenario> <baseline-version> "" --artifacts <artifacts>

Example::

  # Compare PyPI versions 0.50.0 vs 0.51.0
  scripts/perf-run-scenario span ddtrace==0.50.0 ddtrace==0.51.0 --artifacts ./artifacts/

  # Compare PyPI version 0.50.0 vs your local changes
  scripts/perf-run-scenario span ddtrace==0.50.0 . --artifacts ./artifacts/

  # Compare git branch 1.x vs git branch my-feature
  scripts/perf-run-scenario span Datadog/dd-trace-py@1.x Datadog/dd-trace-py@my-feature --artifacts ./artifacts/

  # Run benchmark on a single version without comparison
  scripts/perf-run-scenario span ddtrace==0.51.0 "" --artifacts ./artifacts/


Profiling
~~~~~~~~~

Profiling data can be collected from each scenario using `viztracer`_. With ``scripts/run-benchmarks``, pass ``--profile``::

  scripts/run-benchmarks --scenario span --configs start-finish --profile --artifacts ./benchmark-artifacts/

When using ``scripts/perf-run-scenario`` directly, set ``PROFILE_BENCHMARKS=1``::

  PROFILE_BENCHMARKS=1 scripts/perf-run-scenario span ddtrace==2.8.4 . --artifacts ./artifacts/

One ``viztracer`` output will be created for every scenario run in the artifacts directory.

Use ``scripts/perf-analyze`` to inspect profiling results without needing to open a browser::

  # Top functions by exclusive time
  scripts/perf-analyze artifacts/ --profile-top 20 --filter ddtrace --min-calls 1000

  # Side-by-side baseline vs candidate diff
  scripts/perf-analyze artifacts/ --profile-compare --filter ddtrace --min-calls 1000

Alternatively, you can use the ``viztracer`` tooling directly to view a timeline or flamegraph:

Some examples::

  # Install viztracer
  pip install -U viztracer

  # Load a specific scenario in your browser
  vizviewer artifacts/<run-id>/<scenario_name>/<version>/viztracer/<config_name>.json

  # Load a flamegraph of a specific scenario
  vizviewer --flamegraph artifacts/<run-id>/<scenario_name>/<version>/viztracer/<config_name>.json

  # Combine all processes/threads into a single flamegraph
  jq '{"traceEvents": [.traceEvents[] | .pid = "1" | .tid = "1"]}' <config_name>.json > combined.json
  vizviewer --flamegraph combined.json

Using the ``vizviewer`` UI you can inspect the profile/timeline from each process, as well as execute SQL, like the following::

  SELECT IMPORT("experimental.slices");
  SELECT
    name,
    count(*) as calls,
    sum(dur) as total_duration,
    avg(dur) as avg_duration,
    min(dur) as min_duration,
    max(dur) as max_duration
  FROM experimental_slice_with_thread_and_process_info
  WHERE name like '%/ddtrace/%'
  group by name
  having calls > 500
  order by total_duration desc


See `viztracer`_ documentation for more details.

Scenarios
^^^^^^^^^

.. include:: ../benchmarks/threading/README.rst


.. _viztracer: https://viztracer.readthedocs.io/en/stable/basic_usage.html#display-report
