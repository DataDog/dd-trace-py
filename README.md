# `ddtrace`

[![CircleCI](https://circleci.com/gh/DataDog/dd-trace-py/tree/1.x.svg?style=svg)](https://circleci.com/gh/DataDog/dd-trace-py/tree/1.x)
[![PypiVersions](https://img.shields.io/pypi/v/ddtrace.svg)](https://pypi.org/project/ddtrace/)
[![Pyversions](https://img.shields.io/pypi/pyversions/ddtrace.svg?style=flat)](https://pypi.org/project/ddtrace/)

<img align="right" src="https://user-images.githubusercontent.com/6321485/167082083-53f6e48f-1843-4708-9b98-587c94f7ddb3.png" alt="bits python" width="200px"/>

This repository contains `ddtrace`, Datadog's APM client Python package. `ddtrace` contains APIs to automatically or
manually [trace](https://docs.datadoghq.com/tracing/visualization/#trace) and
[profile](https://docs.datadoghq.com/tracing/profiler/) Python applications.

These features power [Distributed Tracing](https://docs.datadoghq.com/tracing/),
 [Continuous Profiling](https://docs.datadoghq.com/tracing/profiler/),
 [Error Tracking](https://docs.datadoghq.com/tracing/error_tracking/),
 [Continuous Integration Visibility](https://docs.datadoghq.com/continuous_integration/),
 [Deployment Tracking](https://docs.datadoghq.com/tracing/deployment_tracking/),
 [Code Hotspots](https://docs.datadoghq.com/tracing/profiler/connect_traces_and_profiles/) and more.

To get started, check out the [setup documentation][setup docs].

For advanced usage and configuration, check out the [API documentation][api docs].

Confused about the terminology of APM? Take a look at the [APM Glossary][visualization docs].

[setup docs]: https://docs.datadoghq.com/tracing/setup/python/
[api docs]: https://ddtrace.readthedocs.io/
[visualization docs]: https://docs.datadoghq.com/tracing/visualization/

## Development

### Contributing

See [the contributing docs](https://ddtrace.readthedocs.io/en/stable/contributing.html) first.

### Git hooks

In CI, the [pre_check](pre_check) job ensures consistency in coding style and
formatting as well as type checking.

For local development, the repository includes these same checks as custom
scripts that run as Git pre-commit hooks.

To enable all pre-commit hooks in your cloned repository, run the following command:

    $ hooks/autohook.sh install
    
[pre_check]: https://github.com/DataDog/dd-trace-py/blob/5b97489e2b073fa773819904cdef26981a5a28df/.circleci/config.yml#L293-L315

### Development environment

#### Docker

Install [Docker](docker) on your development environment for running tests
locally.

[docker]: https://www.docker.com/products/docker

#### Python

The library supports multiple Python runtime versions and platforms. See [supported runtimes](https://ddtrace.readthedocs.io/en/stable/versioning.html#supported-runtimes) for the full list. You have two options for preparing a local development environment. You can use pyenv to install Python runtimes on your local machine or you can use the `datadog/dd-trace-py` Docker image which includes all runtimes.

##### Option #1: pyenv

1. [Install pyenv](https://github.com/pyenv/pyenv#getting-pyenv)
2. Install Python runtimes with pyenv
3. [Optional] [Install pyenv-virtualenv](https://github.com/pyenv/pyenv-virtualenv)

To install all the tested Python runtimes:

    cd docker; pyenv local | xargs -L 1 pyenv install
    
We recommend using a virtual environment for local development. 

If you followed the optional step to install the `pyenv-virtualenv` plugin, you can use it to manage your Python virtual environments:

    pyenv virtualenv 3.9.11 ddtrace
    pyenv activate ddtrace

Before running tests, you will need to install the following test requirements:

    pip install riot tox 

We use [tox][tox] as well as [riot][riot], a new tool that we developed for
addressing our specific needs with an ever growing matrix of tests. You can list
the tests managed by each:

    $ tox -l
    $ riot list

We include a helper script to run all tox environments matching a regular expression:

    $ scripts/run-tox-scenario '^futures_contrib-'
    
[tox]: https://github.com/tox-dev/tox/
[riot]: https://github.com/DataDog/riot/

##### Option #2: datadog/dd-trace-py

Use the provided script to start the
[datadog/dd-trace-py](https://hub.docker.com/r/datadog/dd-trace-py) image with
volumes mapped correctly for local development:

    scripts/ddtest
    
### Testing

#### Docker services

Many integration tests require services such as PostgreSQL, MySQL, Redis and more. The CI environment uses Docker to start required services before integration tests are run. The repository includes a [docker-compose.yml](https://github.com/DataDog/dd-trace-py/blob/1.x/docker-compose.yml) where all these services are specified and configured.

Instead of starting all Docker services, choose the ones that are required for
the test job relevant to your changes:

1. Find the relevant job in the CircleCI [config.yml](ci_config).
2. Find the optional `docker_services` parameter in the `run_test` command for the job.
3. Use docker-compose to start these services.

For example, the steps for the [psycopg](psycopg_ci_job) job is specified as follows:

``` yaml
    steps:
      - run_test:
          pattern: "psycopg"
          snapshot: true
          docker_services: "postgres"
```

[ci_config]: https://github.com/DataDog/dd-trace-py/blob/1.x/.circleci/config.yml
[psycopg_ci_job]: https://github.com/DataDog/dd-trace-py/blob/5b97489e2b073fa773819904cdef26981a5a28df/.circleci/config.yml#L857-L864

Before running the psycopg integration tests locally then you will need to start the `postgres` Docker service:

    $ docker-compose up -d postgres

#### Running tests

##### riot

2. Create the base virtual environments: `riot -v generate`.
3. You can list the available test suites with `riot list`.
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

##### tox

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
