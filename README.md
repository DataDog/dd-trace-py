# dd-trace-py

[![CircleCI](https://circleci.com/gh/DataDog/dd-trace-py/tree/master.svg?style=svg)](https://circleci.com/gh/DataDog/dd-trace-py/tree/master)
[![Pyversions](https://img.shields.io/pypi/pyversions/ddtrace.svg?style=flat)](https://pypi.org/project/ddtrace/)
[![PypiVersions](https://img.shields.io/pypi/v/ddtrace.svg)](https://pypi.org/project/ddtrace/)
[![OpenTracing Badge](https://img.shields.io/badge/OpenTracing-enabled-blue.svg)](https://ddtrace.readthedocs.io/en/stable/installation_quickstart.html#opentracing)

`ddtrace` is Datadog's tracing library for Python.  It is used to trace requests
as they flow across web servers, databases and microservices so that developers
have great visibility into bottlenecks and troublesome requests.

## Getting Started

For a basic product overview, installation and quick start, check out our
[setup documentation][setup docs].

For more advanced usage and configuration, check out our [API
documentation][api docs].

For descriptions of terminology used in APM, take a look at the [official
documentation][visualization docs].

[setup docs]: https://docs.datadoghq.com/tracing/setup/python/
[api docs]: https://ddtrace.readthedocs.io/
[visualization docs]: https://docs.datadoghq.com/tracing/visualization/


## Development

### Contributing

See [docs/contributing.rst](docs/contributing.rst).


### Pre-commit Hooks

The tracer library uses formatting/linting tools including black, flake8, and mypy.
While these are run in each CI pipeline for pull requests, they are automated to run
when you call `git commit` as pre-commit hooks to catch any formatting errors before 
you commit. To initialize the pre-commit hook script to run in your development 
branch, run the following command:

    $ hooks/autohook.sh install

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


#### Running Tests in docker

Once your docker-compose environment is running, you can use the shell script to
execute tests within a Docker image. You can start the container with a bash shell: 

    $ scripts/ddtest

You can now run tests as you would do in your local environment. We use
[tox][tox] as well as [riot][riot], a new tool that we developed for addressing
our specific needs with an ever growing matrix of tests. You can list the tests
managed by each:

    $ tox -l
    $ riot list
    
You can run multiple tests by using regular expressions:

    $ scripts/run-tox-scenario '^futures_contrib-'
    $ riot run psycopg
    
[tox]: https://github.com/tox-dev/tox/
[riot]: https://github.com/DataDog/riot/

### Continuous Integration

We use CircleCI 2.0 for our continuous integration.


#### Configuration

The CI tests are configured through [config.yml](.circleci/config.yml).


#### Running Locally

The CI tests can be run locally using the `circleci` CLI. More information about
the CLI can be found at https://circleci.com/docs/2.0/local-cli/.

After installing the `circleci` CLI, you can run jobs by name. For example:

    $ circleci build --job django
