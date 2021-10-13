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

### Set up your environment

1. Download the dd-trace-py repository locally (e.g, `git clone`)
2. Check if python is installed in your environment: `python --version`. If python is not installed, download it: https://www.python.org/downloads/
3. The tests for this project run on various versions of python. We recommend using a Python Version Management tool, such as Pyenv to utilize multiple versions of Python
4. Install Pyenv following these instructions: https://github.com/pyenv/pyenv#installation
5. Run `pyenv install` for the following versions of Python: 3.9.1, 2.7.18, 3.5.10, 3.6.12, 3.7.9, 3.8.7, 3.10.0
6. Run `pyenv global` for the same list of versions: 3.9.1, 2.7.18, 3.5.10, 3.6.12, 3.7.9, 3.8.7, 3.10.0

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
and more. We use `docker` and `docker-compose` to run the services in our CI
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

#### Running Tests locally

To run tests locally with riot, first install riot: `pip install riot`.
Then, create the base virtual environments: `riot -v generate`.
You can generate a list of all the available test suites by running `riot list`.
Certain tests (such as the `contrib` tests) might require service containers to be running in order to emulate the necessary testing environment. You can specify individual containers to spin up (rather than the entire `compose` file) using `docker-compose up -d <SERVICE_NAME>`. `<SERVICE_NAME>` should match one of the services specified in the `docker-compose.yml` file (e.g, `elasticsearch`, `cassandra`, `consul`, etc).
You can run a test suite with `riot -v run -s -x <TEST_SUITE_NAME>`. To limit the tests to a particular version of Python, use the -p flag: `riot -v run -p <PYTHON_VERSION>`.
Note that the `run` command uses regex syntax, so in some cases you may want to use the following syntax: `^<TEST_SUITE_NAME>$` where `^` signifies the start of a string and `$` signifies the end of a string (for example, if you run `riot -v run -s -x redis`, both the redis and rediscluster test suites will run. You can use `riot -v run -s -x ^redis$` to ensure only the redis suite is run).

The APM test agent is an application which emulates the APM endpoints of the Datadog agent which can be used for testing Datadog APM client libraries. You can use the test agent as a proxy to the Datdaog Agent either via the `--agent-url `commandline argument or by the `DD_TRACE_AGENT_URL` or `DD_AGENT_URL` environment variables. You can spin up the `testagent` container along with any of the other service containers: `docker-compose up -d testagent <SERVICE_CONTAINER>`. Then run the test agent as a proxy in your tests: `DD_TRACE_AGENT_URL=http://<IP>:<PORT>/ riot -v run <RUN_FLAGS> --pass-env <TEST_SUITE_NAME>`. If you were to run the redis test suite using the test agent you might run something like this: `DD_TRACE_AGENT_URL=http://localhost:9126/ riot -v run -p 3.9 -s -x --pass-env redis`

Read more about the APM test agent: https://github.com/datadog/dd-apm-test-agent#readme

### Continuous Integration

We use CircleCI 2.0 for our continuous integration.

#### Configuration

The CI tests are configured through [config.yml](.circleci/config.yml).

#### Running Locally

The CI tests can be run locally using the `circleci` CLI. More information about
the CLI can be found at https://circleci.com/docs/2.0/local-cli/.

After installing the `circleci` CLI, you can run jobs by name. For example:

    $ circleci build --job django
