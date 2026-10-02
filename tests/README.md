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
    skip: # Skip the suite, even when needed
    env: # Environment variables to pass to the runner
    venvs_per_job: # Number of dependency environments assigned to each generated job
    retry: # The number of retries for the job
    timeout: # The timeout for the job
    pattern: # The pattern/environment name (if different from the suite name)
    paths: # The paths/components that trigger the job
    services: # The services to start before running the suite, defined in .gitlab/services.yml
    no_proxy: # Add the suite's external test domains to NO_PROXY and no_proxy
    matrix: # Shared configuration and named dependency variants
```

For example

```yaml
suites:
  profile:
    env:
      DD_TRACE_AGENT_URL: ''
    venvs_per_job: 1
    retry: 2
    pattern: profile
    paths:
      - '@bootstrap'
      - '@core'
      - '@profiling'
      - tests/profiling/*
    services:
      - redis
```

Components do not need to be declared within the same `suitespec.yml` file. They
can be declared in any file within the `/tests` sub-tree. The CI configuration
generator will aggregate all the components and suites to build the full test
suite specification and resolve the components after that.

### Discovered dependencies

A suite's `paths` only need the code it tests, plus the dependencies that
imports cannot reveal. A reference to a component declared in the same
`suitespec.yml` is shorthand for that component's patterns, and is replaced by
them when the file is loaded. The suite's explicit patterns that point into
`ddtrace/` then pull in the components that the matching sources import
directly. For example, the debugger suite references `@debugging`, whose
sources import the tracer, core and remote configuration code, so changes
there trigger the suite without being listed.

Discovery goes one hop only: the components it adds are not expanded further,
and neither are references to components declared in other `suitespec.yml`
files, such as `@bootstrap`. Those are taken as they are.

An imported file belongs to the component with the most specific matching
pattern (an exact path beats a glob, a longer prefix beats a shorter one).
Discovery counts imports inside functions, but not imports under
`TYPE_CHECKING`. It also counts imports in Cython files, and names that a
package resolves lazily through a module-level `__getattr__` and a
name-to-module dict.

Declare these in `paths` explicitly, because imports cannot reveal them:

- `@bootstrap`, which tests enter through `ddtrace-run` and `sitecustomize`;
- integration components, which `ddtrace/_monkey.py` imports by name;
- code loaded dynamically, such as product plugins and the Data Streams
  integrations;
- dependencies of a component declared in another `suitespec.yml`.

Discovery only adds patterns: the declared ones always apply.

### Auditing dependencies with runtime coverage

`scripts/audit_suite_dependencies.py` complements import discovery with files
observed during test execution. It reads coverage.py JSON reports and emits a
YAML fragment containing only additions to a suite's resolved triggers. It does
not modify suitespecs or change CI selection. This uses the existing pytest-cov
dependency; pytest-testmon is not required.

Capture one complete suite environment at a time. List the environments, choose
a hash, and give each run a separate report path:

```bash
scripts/run-tests --list tests/debugging/
scripts/run-tests --venv <environment-hash> -- \
  -o addopts= --no-ddtrace --cov=ddtrace --cov=tests/ \
  --cov-report=json:.cache/debugger-py310.json
```

The empty `addopts` override disables the repository's `--cov-append`, preventing
other suite runs from contaminating the report. `--no-ddtrace` disables Datadog
Test Optimization, including test skipping. Do not use test filters or consume
reports from failed runs. For suites whose commands disable coverage, remove
that option in a local capture configuration first. For environments with
multiple commands, capture each command separately; do not overwrite a report.

Union reports from the same suite across Python versions, dependency versions,
platforms, and relevant configurations:

```bash
scripts/audit_suite_dependencies.py --suite debugging::debugger \
  --coverage .cache/debugger-py310.json \
  --coverage .cache/debugger-py314.json \
  --source-root /home/bits/project > .cache/debugger-additions.yml
```

Repo-relative paths and absolute paths under the current checkout are recognized
automatically. Repeat `--source-root` for other captured checkout paths or
site-packages directories containing ddtrace. Other external paths are ignored.
An executed ddtrace path without a mapping, or a report with no executed ddtrace
files, is rejected to catch incomplete path mappings and empty captures.

The output uses fully qualified suite names, such as `debugging::debugger`.
Append its `paths` entries to the corresponding suite in its original suitespec;
do not replace the suite's existing paths with this fragment. Missing files are
listed on stderr. Add `--check` to exit with status 1 if dependencies are missing;
invalid input exits with status 2. Without `--check`, valid input exits with
status 0 even when it produces additions.

Files already covered by explicit patterns, include-always components, or import
discovery need no additions. Missing files map to their most specific component
owners, retaining all tied owners; files without an owner use exact paths.
Output is sorted and deduplicated for review. Component references intentionally
cover future files matching the same patterns.

Runtime coverage is evidence for additions, not removals. Unexecuted paths,
native code, subprocesses without coverage collection, and sources excluded by
the coverage configuration remain blind spots. In particular, the default
configuration excludes `ddtrace/vendor/*`, and the test harness currently warns
that subprocess coverage is broken. Startup and shared fixture execution may
also produce broad dependencies. Retain explicit dependencies and review each
suggestion; an empty report of additions does not establish completeness.

For standard test suites, `venvs_per_job` is the target number of dependency
environments per generated job. The job count is the environment count divided by
this value and rounded up, with a limit of 25 jobs per suite. Lower values increase
parallelism. By default, `venvs_per_job` is the suite's total environment count, so
omitting it runs the suite as one job. Do not set `parallelism` directly.

Suites using `ddtest: true` shard each dependency environment with `ddtest_nodes`
instead. They must not set `venvs_per_job`.

Set `no_proxy: true` only for suites whose VCR-backed or network-behavior tests need to bypass the
CI proxy. The generated job preserves existing `NO_PROXY` and `no_proxy` values and appends the
standard test-domain exclusions.
