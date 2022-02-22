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

The scenario class inherits from ``bm.Scenario`` and includes the configurable variables using ``bm.var``. The execution of the benchmark uses the ``run()`` generator function to yield a function that will handle the execution of a specified number of loops.

Example
~~~~~~~

``scenario.py``
+++++++++++++++

::

  import bm


  class MyScenario(bm.Scenario):
      size = bm.var(type=int)

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

The version specifiers can reference published versions on PyPI or git
repositories.

Example::

  scripts/perf-run-scenario span ddtrace==0.50.0 ddtrace==0.51.0 ./artifacts/
  scripts/perf-run-scenario span Datadog/dd-trace-py@1.x Datadog/dd-trace-py@my-feature ./artifacts/


Scenarios
^^^^^^^^^

.. include:: ../benchmarks/threading/README.rst
