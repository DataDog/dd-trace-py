# dd-trace-py

[![CircleCI](https://circleci.com/gh/DataDog/dd-trace-py/tree/master.svg?style=svg)](https://circleci.com/gh/DataDog/dd-trace-py/tree/master)
[![Pyversions](https://img.shields.io/pypi/pyversions/ddtrace.svg?style=flat)](https://pypi.org/project/ddtrace/)
[![PypiVersions](https://img.shields.io/pypi/v/ddtrace.svg)](https://pypi.org/project/ddtrace/)
[![OpenTracing Badge](https://img.shields.io/badge/OpenTracing-enabled-blue.svg)](http://pypi.datadoghq.com/trace/docs/installation_quickstart.html#opentracing)

`ddtrace` is Datadog's tracing library for Python.  It is used to trace requests
as they flow across web servers, databases and microservices so that developers
have great visiblity into bottlenecks and troublesome requests.

## Getting Started

For a basic product overview, installation and quick start, check out our
[setup documentation][setup docs].

For more advanced usage and configuration, check out our [API
documentation][pypi docs].

For descriptions of terminology used in APM, take a look at the [official
documentation][visualization docs].

[setup docs]: https://docs.datadoghq.com/tracing/setup/python/
[pypi docs]: http://pypi.datadoghq.com/trace/docs/
[visualization docs]: https://docs.datadoghq.com/tracing/visualization/


## Development


### Testing


#### Environment

The test suite requires many backing services such as PostgreSQL, MySQL, Redis
and more. We use ``docker`` and ``docker-compose`` to run the services in our CI
and for development. To run the test matrix, please [install docker][docker] and
[docker-compose][docker-compose] using the instructions provided by your platform. Then
launch them through:

    $ docker-compose up -d


[docker]: https://www.docker.com/products/docker
[docker-compose]: https://www.docker.com/products/docker-compose


#### Running the Tests in your local environment

Once docker is up and running you should be able to run the tests. To launch a
single test manually. For example to run the tests for `redis-py` 2.10 on Python
3.5 and 3.6:

    $ tox -e '{py35,py36}-redis{210}'

To see the defined test commands see `tox.ini`.

To launch the complete test matrix run:

    $ tox


#### Running Tests in docker

If you prefer not to setup your local machine to run tests, we provide a preconfigured docker image.
Note that this image is the same used in CircleCI to run tests.

You still need docker containers running additional services up and running.

Run the test runner

    $ docker-compose run --rm testrunner

Now you are in a bash shell. You can now run tests as you would do in your local environment:

    $ tox -e '{py35,py36}-redis{210}'

We also provide a shell script to execute commands in the provided container.

For example to run the tests for `redis-py` 2.10 on Python 3.5 and 3.6:

    $ ./scripts/ddtest tox -e '{py35,py36}-redis{210}'


### Continuous Integration

We use CircleCI 2.0 for our continuous integration.


#### Configuration

The CI tests are configured through [config.yml](.circleci/config.yml).


#### Running Locally

The CI tests can be run locally using the `circleci` CLI. More information about
the CLI can be found at https://circleci.com/docs/2.0/local-jobs/.

After installing the `circleci` CLI, you can run jobs by name. For example:

    $ circleci build --job django


### Benchmarking

When two or more approaches must be compared, please write a benchmark in the
[benchmark.py](tests/benchmark.py) module so that we can measure the efficiency
of the algorithm. To run your benchmark, just:

    $ python -m tests.benchmark
