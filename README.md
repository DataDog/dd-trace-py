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

### Pre-commit Hooks

**NOTE**: Pre-commit hooks are optional and provided as a convenience for contributors. 
If pre-commit hooks fail to run in your development environment, you can uninstall them by deleting the symlink created by the installation script:

    $ rm .git/hooks/pre-commit
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

### Testing

Run the test suite with the following command:

    $ scripts/ddtest riot run

The test suite is huge and running the entire thing is probably not what you want to do.
See the [testing guidelines](docs/contributing-testing.rst) for more on running specific tests.

### Release Notes

This project follows [semver](https://semver.org/) and so bug fixes, breaking
changes, new features, etc must be accompanied by a release note.

See the [contributing docs](https://ddtrace.readthedocs.io/en/stable/contributing.html) for
instructions on generating, writing, formatting, and styling release notes.
