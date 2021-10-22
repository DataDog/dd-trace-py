# dd-trace-py

[![CircleCI](https://circleci.com/gh/DataDog/dd-trace-py/tree/master.svg?style=svg)](https://circleci.com/gh/DataDog/dd-trace-py/tree/master)
[![Pyversions](https://img.shields.io/pypi/pyversions/ddtrace.svg?style=flat)](https://pypi.org/project/ddtrace/)
[![PypiVersions](https://img.shields.io/pypi/v/ddtrace.svg)](https://pypi.org/project/ddtrace/)
[![OpenTracing Badge](https://img.shields.io/badge/OpenTracing-enabled-blue.svg)](https://ddtrace.readthedocs.io/en/stable/installation_quickstart.html#opentracing)

`ddtrace` is Datadog's tracing library for Python. It is used to trace requests
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

### Set up your environment

#### Set up docker

The test suite requires many backing services such as PostgreSQL, MySQL, Redis
and more. We use `docker` and `docker-compose` to run the services in our CI
and for development. To run the test matrix, please [install docker][docker] and
[docker-compose][docker-compose] using the instructions provided by your platform. Then
launch them through:

    $ docker-compose up -d

[docker]: https://www.docker.com/products/docker
[docker-compose]: https://www.docker.com/products/docker-compose

#### Set up Python

1. Clone the repository locally: `git clone https://github.com/DataDog/dd-trace-py`
2. The tests for this project run on various versions of Python. We recommend
   using a Python version management tool, such as
   [pyenv](https://github.com/pyenv/pyenv), to utilize multiple versions of
   Python. Install Pyenv: https://github.com/pyenv/pyenv#installation
3. Install the relevant versions of Python in Pyenv: `pyenv install 3.9.1, 2.7.18, 3.5.10, 3.6.12, 3.7.9, 3.8.7, 3.10.0`
4. Make those versions available globally: `pyenv global 3.9.1, 2.7.18, 3.5.10, 3.6.12, 3.7.9, 3.8.7, 3.10.0`

### Testing

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

#### Running Tests locally

1. Install riot: `pip install riot`.
2. Create the base virtual environments: `riot -v generate`.
3. You can list the available test suites with `riot list`.
4. Certain tests might require running service containers in order to emulate
   the necessary testing environment. You can spin up individual containers with
   `docker-compose up -d <SERVICE_NAME>`, where `<SERVICE_NAME>` should match a
   service specified in the `docker-compose.yml` file.
5. Run a test suite: `riot -v run <RUN_FLAGS> <TEST_SUITE_NAME>`.
   1. Optionally, use the `-s` and `-x` flags: `-s` prevents riot from
      reinstalling the dev package; `-x` forces an exit after the first failed
      test suite. To limit the tests to a particular version of Python, use the
      `-p` flag: `riot -v run -p <PYTHON_VERSION>`.

The `run` command uses regex syntax, which in some cases will cause multiple
test suites to run. Use the following syntax to ensure only an individual suite
runs: `^<TEST_SUITE_NAME>$` where `^` signifies the start of a string and `$`
signifies the end of a string. For example, use `riot -v run -s -x ^redis$` to
run only the redis suite.

#### Use the APM Test Agent

The APM test agent can emulate the APM endpoints of the Datadog agent. Spin up
the `testagent` container along with any other service container:

    $ docker-compose up -d testagent <SERVICE_CONTAINER>

Run the test agent as a proxy in your tests:

    $ DD_TRACE_AGENT_URL=http://localhost:9126/ riot -v run <RUN_FLAGS> --pass-env <TEST_SUITE_NAME>

`--pass-env` injects the environment variables of the current shell session into
the command. Here's an example command for running the redis test suite along
with the test agent, limited to tests for Python 3.9:

    $ DD_TRACE_AGENT_URL=http://localhost:9126/ riot -v run -p 3.9 -s -x --pass-env '^redis$'

Read more about the APM test agent:
https://github.com/datadog/dd-apm-test-agent#readme

### Continuous Integration

We use CircleCI 2.0 for our continuous integration.

#### Configuration

The CI tests are configured through [config.yml](.circleci/config.yml).

#### Running Locally

The CI tests can be run locally using the `circleci` CLI. More information about
the CLI can be found at https://circleci.com/docs/2.0/local-cli/.

After installing the `circleci` CLI, you can run jobs by name. For example:

    $ circleci build --job django

### Release Notes

This project follows [semver](https://semver.org/) and so bug fixes, breaking
changes, new features, etc must be accompanied by a release note. To generate a
release note:

    $ riot run reno new <short-description-of-change>

Document the changes in the generated file, remove the irrelevant sections and
commit the release note with the change.
