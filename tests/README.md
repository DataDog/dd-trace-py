# Dynamic Job Runs

This repository makes use of dynamic features of CI providers to run only the
jobs that are necessary for the changes in the pull request. This is done by
giving a logical description of the test suites in terms of _components_ and _suites_.
These are declared in a modular way in `suitespec.yml` files within the `/tests`
sub-tree. When the CI configuration is generated, these files are aggregated to
build the full test suite specification.

## Components

A component is a logical grouping of tests that can be run independently of other
components. For example, a component could be a test suite for a specific
package, or a test suite for a specific feature, e.g. the tracer, the profiler,
etc... . Inside a `suitespec.yml` file, a component declares the patterns of
files that should trigger the tests in that component.

```yaml
components:
  tracer:
  - ddtrace/tracer/*.py
  ...
```

Some file patterns need to trigger all the tests in the test suite. This is
generally the case for setup files, such as `setup.py`, `pyproject.toml`, etc...
Tests harness sources too need to trigger all tests in general. To avoid
declaring these patterns, or the component explicitly in the suites, their name
can be prefixed with `$`. Components prefixed with `$` will be applied to _all_
suites automatically.

## Suites

A suite declares what job needs to run when the associated paths are modified.
The suite schema is as follows:

```yaml
  suite_name:
    runner: # The test runner (riot | hatch)
    skip: # Skip the suite, even when needed
    env: # Environment variables to pass to the runner
    parallelism: # The parallel degree of the job
    retry: # The number of retries for the job
    timeout: # The timeout for the job
    pattern: # The pattern/environment name (if different from the suite name)
    paths: # The paths/components that trigger the job
    services: # The services to start before running the suite, defined in .gitlab/services.yml
```

For example

```yaml
suites:
  profile:
    runner: riot
    env:
      DD_TRACE_AGENT_URL: ''
    parallelism: 20
    retry: 2
    pattern: profile$|profile-v2
    paths:
      - '@bootstrap'
      - '@core'
      - '@profiling'
      - tests/profiling/*
      - tests/profiling_v2/*
    services:
      - redis
```

Components do not need to be declared within the same `suitespec.yml` file. They
can be declared in any file within the `/tests` sub-tree. The CI configuration
generator will aggregate all the components and suites to build the full test
suite specification and resolve the components after that.
