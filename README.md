# dd-trace-py

[![CircleCI](https://circleci.com/gh/DataDog/dd-trace-py.svg?style=svg&circle-token=f9bf80ce9281bc638c6f7465512d65c96ddc075a)](https://circleci.com/gh/DataDog/dd-trace-py)

## Testing

The test suite requires many backing services (PostgreSQL, MySQL, Redis, ...) and we're using
Docker to start the service in the CI and in the developer machine. To launch properly the
test matrix, please [install Docker][1] in your environment.

Then, simply::

    $ rake test

## Benchmark

When two or more approaches must be compared, please write a benchmark in the ``tests/benchmark.py``
module so that we can keep track of the most efficient algorithm. To run your benchmark, just::

    $ python -m tests.benchmark

[1]: https://www.docker.com/products/docker
