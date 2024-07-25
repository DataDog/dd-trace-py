Benchmarks
----------

These benchmarks are intended to provide stable and reproducible measurements of the performance characteristics of the ``ddtrace`` library. A scenario is defined using a simple Python framework. Docker is used to build images for the execution of scenarios against different versions of ``ddtrace``.

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

Run scenario
^^^^^^^^^^^^

The scenario can be run using the built image to compare two versions of the library and save the results in a local artifacts folder::

  scripts/perf-run-scenario <scenario> <version> <version> <artifacts>

The version specifiers can reference published versions on PyPI, git repositories, or `.` for your local version.

Example::

  # Compare PyPI versions 0.50.0 vs 0.51.0
  scripts/perf-run-scenario span ddtrace==0.50.0 ddtrace==0.51.0 ./artifacts/

  # Compare PyPI version 0.50.0 vs your local changes
  scripts/perf-run-scenario span ddtrace==0.50.0 . ./artifacts/

  # Compare git branch 1.x vs git branch my-feature
  scripts/perf-run-scenario span Datadog/dd-trace-py@1.x Datadog/dd-trace-py@my-feature ./artifacts/


Profiling
~~~~~~~~~

You may also generate profiling data from each scenario using `viztracer`_ by providing the ``PROFILE_BENCHMARKS=1`` environment variable.

Example::

  # Compare and profile PyPI version 2.8.4 against your local changes, and store the results in ./artifacts/
  PROFILE_BENCHMARKS=1 scripts/perf-run-scenario span ddtrace==2.8.4 . ./artifacts/

One ``viztracer`` output will be created for every scenario run in the artifacts directory.

You can use the ``viztracer`` tooling to combine or inspect the resulting files locally

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
