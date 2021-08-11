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

The scenario class inherits from ``bm.Scenario`` and includes the configurable variables using ``bm.var``. The execution of the benchmark uses the ``run()`` generator function to yield a function that will handle the execution of a specified number of loops. The scenario class must also be decorated with ``@bm.register`` to ensure that it is run.

Example
~~~~~~~

``scenario.py``
+++++++++++++++

::

  import bm


  @bm.register
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


.. _docker:

Docker
^^^^^^

Assuming you have added ``benchmarks/<scenario>``, you can build an image for scenario::

  docker build \
    -t <scenario> \
    --build-arg SCENARIO=<scenario> \
    .

The image supports the comparison of two versions of the library::

  docker run -it --rm \
    -e DDTRACE_INSTALL_V1="ddtrace" \
    -e DDTRACE_INSTALL_V2="ddtrace==0.50.0" \
    <scenario>

The environment variables for installing the library also support git urls.
