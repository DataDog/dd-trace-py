dd-trace-py
===========

|CircleCI|

For API docs see http://pypi.datadoghq.com/trace/docs/

Versions
--------

Tracing client libraries will follow `semver <http://semver.org>`__.
While we are less than version 1.0, we'll increment the minor version
number for backwards incompatible and significant changes. We'll
increment the bugfix version for other changes.

This library is in beta so please pin your version numbers and do phased
rollouts.

`changelog <https://github.com/DataDog/dd-trace-py/releases>`__

Development
-----------

Testing
~~~~~~~

The test suite requires many backing services (PostgreSQL, MySQL, Redis,
...) and we're using ``docker`` and ``docker-compose`` to start the
service in the CI and in the developer machine. To launch properly the
test matrix, please `install
docker <https://www.docker.com/products/docker>`__ and
`docker-compose <https://www.docker.com/products/docker-compose>`__
using the instructions provided by your platform.

You can launch the test matrix using the following rake command:

::

    $ rake test

Benchmarks
~~~~~~~~~~

When two or more approaches must be compared, please write a benchmark
in the ``tests/benchmark.py`` module so that we can keep track of the
most efficient algorithm. To run your benchmark, just:

::

    $ python -m tests.benchmark

.. |CircleCI| image:: https://circleci.com/gh/DataDog/dd-trace-py.svg?style=svg&circle-token=f9bf80ce9281bc638c6f7465512d65c96ddc075a
   :target: https://circleci.com/gh/DataDog/dd-trace-py
