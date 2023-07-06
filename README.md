# `ddtrace`

[![CircleCI](https://circleci.com/gh/DataDog/dd-trace-py/tree/1.x.svg?style=svg)](https://circleci.com/gh/DataDog/dd-trace-py/tree/1.x)
[![PypiVersions](https://img.shields.io/pypi/v/ddtrace.svg)](https://pypi.org/project/ddtrace/)
[![Pyversions](https://img.shields.io/pypi/pyversions/ddtrace.svg?style=flat)](https://pypi.org/project/ddtrace/)

<img align="right" src="https://user-images.githubusercontent.com/6321485/167082083-53f6e48f-1843-4708-9b98-587c94f7ddb3.png" alt="bits python" width="200px"/>

This library powers [Distributed Tracing](https://docs.datadoghq.com/tracing/),
 [Continuous Profiling](https://docs.datadoghq.com/tracing/profiler/),
 [Error Tracking](https://docs.datadoghq.com/tracing/error_tracking/),
 [Continuous Integration Visibility](https://docs.datadoghq.com/continuous_integration/),
 [Deployment Tracking](https://docs.datadoghq.com/tracing/deployment_tracking/),
 [Code Hotspots](https://docs.datadoghq.com/tracing/profiler/connect_traces_and_profiles/),
 [Dynamic Instrumentation](https://docs.datadoghq.com/dynamic_instrumentation/),
 and more.

To get started with tracing, check out the [product documentation][setup docs] or the [glossary][visualization docs].

For advanced usage and configuration information, check out the [library documentation][api docs].

To get started as a contributor, see [the contributing docs](https://ddtrace.readthedocs.io/en/stable/contributing.html) first.

[setup docs]: https://docs.datadoghq.com/tracing/setup/python/
[api docs]: https://ddtrace.readthedocs.io/
[visualization docs]: https://docs.datadoghq.com/tracing/visualization/

## Testing

### Running Tests in docker

The dd-trace-py testrunner docker image allows you to run tests in an environment that matches CI. This is especially useful
if you are unable to install certain test dependencies on your dev machine's bare metal.

Once your docker-compose environment is running, you can use the shell script to
execute tests within a Docker image. You can start the container with a bash shell:

    $ scripts/ddtest

You can now run tests as you would do in your local environment. We use
[riot][riot], a new tool that we developed for addressing
our specific needs with an ever growing matrix of tests. You can list the tests
managed by each:

    $ riot list

You can run multiple tests by using regular expressions:

    $ riot run psycopg

[riot]: https://github.com/DataDog/riot/

### Running Tests locally

1. Install riot: `pip install riot`.
2. Create the base virtual environments: `riot -v generate`.
3. You can list the available test suites with `riot list`.
4. Certain tests might require running service containers in order to emulate
   the necessary testing environment. You can spin up individual containers with
   `docker-compose up -d <SERVICE_NAME>`, where `<SERVICE_NAME>` should match a
   service specified in the `docker-compose.yml` file.
5. Run a test suite: `riot -v run <RUN_FLAGS> <TEST_SUITE_NAME>`.

You can use the `-s` and `-x` flags: `-s` prevents riot from reinstalling the dev package;
`-x` forces an exit after the first failed test suite. To limit the tests to a particular
version of Python, use the `-p` flag: `riot -v run -p <PYTHON_VERSION>`. You can also pass
command line arguments to the underlying test runner (like pytest) with the `--` argument.
For example, you can run a specific test under pytest with
`riot -v run -s gunicorn -- -k test_no_known_errors_occur`

The `run` command uses regex syntax, which in some cases will cause multiple
test suites to run. Use the following syntax to ensure only an individual suite
runs: `^<TEST_SUITE_NAME>$` where `^` signifies the start of a string and `$`
signifies the end of a string. For example, use `riot -v run -s -x ^redis$` to
run only the redis suite.

### Use the APM Test Agent

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
