.. _testing_guidelines:

Testing
=======

Imagine you're making a change to the library.

If your change touches Python code, it should probably include at least one test.

What kind of tests should I write?
----------------------------------

We use heuristics to decide when and what sort of tests to write. For example, a pull request implementing
a new feature should include enough unit tests to cover the feature's "happy path" use cases in addition
to any known likely edge cases. If the feature involves a new form of communication with another component
(like the Datadog Agent or libddwaf), it should probably include at least one integration test exercising
the end-to-end communication.

If a pull request fixes a bug, it should include a test that, on the trunk branch, would replicate the bug.
Seeing this test pass on the fix branch gives us confidence that the bug was actually fixed.

Where do I put my tests?
------------------------

Put your code's tests in the appropriate subdirectory of the ``tests`` directory based on what they are testing.
If your feature is substantially new, you may decide to create a new ``tests`` subdirectory in the interest
of code organization.

.. _capability_test_contracts:

Capability test contracts and isolation
--------------------------------------

These conventions apply to tests owned by ``apm-sdk-capabilities`` in ``.github/CODEOWNERS``.
Apply them to new tests and when changing existing coverage. Preserve other owners' contracts
and co-ownership when consolidating shared tests.

Choose the boundary
^^^^^^^^^^^^^^^^^^^

Start with a behavior: configuration input, initialization, an application action, and its
observable result. Prefer public APIs and emitted spans, headers, metrics, or documented errors.
A test should explain what breaks for an application when it fails.

Use a fresh subprocess for environment configuration read at import time, global singletons,
provider registration, monkey-patching, background workers, shutdown, and fork behavior.
An xdist worker is reused across tests and is not a substitute for a fresh application process.
Do not simulate a clean process by reloading modules or rebuilding the global tracer in-place.

Keep local span/context objects, parsing, encoding, sampling mathematics, and other substantial
algorithms in-process when they do not mutate shared state. Prefer the public entry point.
Direct tests of a private method need a specific complex algorithm or low-level invariant;
frequent reliance on private implementation details is a reason to review the design.

Mock external boundaries or time when necessary. Do not mock the decision or configuration path
that the test claims to validate. For asynchronous behavior, wait for a bounded observable signal
instead of sleeping for an estimated duration.

Write isolated subprocess tests
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Use ``pytest.mark.subprocess`` for self-contained scenarios. Put application imports inside the
test body so the configured environment exists before initialization. Do not request parent
fixtures as arguments: the marker executes the function body in a different process.

.. code-block:: python

    import pytest

    @pytest.mark.subprocess(
        env={"DD_TRACE_PROPAGATION_STYLE_EXTRACT": "datadog"}, timeout=10
    )
    def test_datadog_headers_are_extracted() -> None:
        from ddtrace.propagation.http import HTTPPropagator

        context = HTTPPropagator.extract(
            {"x-datadog-trace-id": "1234", "x-datadog-parent-id": "5678"}
        )
        assert context.trace_id == 1234
        assert context.span_id == 5678

Specify every environment value that determines the scenario. Use ``None`` in the marker's
environment mapping to remove an inherited variable. Related assertions can share a process;
independent startup configurations require independent processes.

When the parent must own a receiver, use the existing subprocess script fixture, pass its
endpoint through the child environment, and assert the return code and captured output.
Use an explicit timeout. Avoid introducing another process-launch helper or raw subprocess call.

Tests that emit data must declare their receiver or test-agent service through the suite.
Do not rely on a developer's running agent, disable the behavior under test to silence errors,
or broadly ignore stderr. Assert expected diagnostics when diagnostics are part of the contract.

Imported assertion helpers also need to work in optimized Python. Ordinary assertions in an
imported helper may be removed by optimization; use explicit failure checks for those helpers
or keep the contract assertions in the subprocess test body.

Use snapshots for emitted contracts
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Use the snapshot marker for a whole test and the context manager only when separate phases need
separate snapshots. Assert critical counts, relationships, values, and absent fields explicitly
when they make the behavior clearer than a large snapshot alone.

Normalize unstable identifiers and timestamps while preserving parent-child relationships and
meaningful event data. Do not patch production methods just to manufacture stable timestamps.
Do not hide meaningful fields with broad ignore lists or regenerate snapshots to resolve an
unexplained mismatch. Keep baseline tokens stable during file or test-name moves.

Each scenario owns its receiver session and cleanup. A skipped or failed test must not leave
writer headers, environment changes, background workers, or child processes for another test.
An expected failure must name a specific known issue, not treat all output mismatches as flaky.

Organize and simplify cases
^^^^^^^^^^^^^^^^^^^^^^^^^^

Group tests by feature, then distinguish startup configuration, behavior/emission, and justified
algorithm coverage where useful. Keep runtime and OpenTelemetry tests in their existing roots.
Do not copy a feature's cases into a second suite solely to obtain coverage from another runner.

Use named parameter cases for defaults, boundaries, invalid input, and meaningful interactions.
Use a Cartesian product only when every interaction matters. Include valid surrounding inputs
when testing a parser branch; an earlier rejected field can prevent the intended branch running.

Write pytest functions with typed arguments and ``-> None``. Type fixtures and shared helpers,
use ``pathlib.Path``/``tmp_path`` and ``pytest.MonkeyPatch``, and prefer ordinary assertions over
new unittest subclasses. Make missing, zero, false, and unchecked expectations distinct.

Before removing overlapping cases, record the behavior each case proves and its retained test.
Check that the retained test fails for an incorrect result. Preserve tests of separate supported
contracts, such as startup configuration and an explicitly supported runtime override API.

Starting contract inventory
^^^^^^^^^^^^^^^^^^^^^^^^^^^

Use this map to find existing coverage before adding a new test; it is not permission to delete
all tests of a similar shape. Record the exact retained test names in a consolidation PR.

.. list-table::
   :header-rows: 1
   :widths: 20 40 40

   * - Contract
     - Existing coverage
     - Boundary to prefer
   * - Service, environment, and version
     - ``tests/tracer/test_env_vars.py`` and ``EnvTracerTestCase`` in ``test_tracer.py``
     - Environment before import; assert resulting span metadata
   * - Propagation configuration and headers
     - ``tests/tracer/test_propagation.py`` and ``tests/integration/test_propagation.py``
     - Fresh startup for configuration; deterministic header cases locally
   * - Sampling decisions
     - ``test_sampler.py``, ``test_single_span_sampling_rules.py``, and integration priority sampling
     - Configured behavior and emitted decisions; local matching/rate algorithms
   * - Runtime metrics
     - ``tests/runtime/`` and ``tests/tracer/runtime/``
     - Public lifecycle and received metrics; local collector algorithms
   * - OpenTelemetry providers and context
     - ``tests/opentelemetry/``
     - Fresh provider initialization; observable context and exported data

Ownership follows the last matching CODEOWNERS rule, including exceptions within a directory.
``tests/integration/test_sampling.py`` and ``test_tracemethods.py`` are adjacent coverage to review
with their current owners; the stale root-level entries do not transfer ownership of those files.

Validate changes with ``scripts/run-tests`` and ``scripts/lint``. Compare collection and execution
time separately from environment setup, verify child-process coverage before removing a coverage
workaround, and exercise isolation-sensitive selections independently and in shuffled order.

How do I run the test suite?
----------------------------

**Prerequisites**

Install and run:

* `docker <https://www.docker.com/products/docker>`_
* `uv <https://docs.astral.sh/uv/getting-started/installation/>`_

**Easy way: Use scripts/run-tests**

The ``scripts/run-tests`` script handles this automatically:

.. code-block:: bash

    # Run test suites for your current changes
    $ scripts/run-tests

    # Run test suites affected by source changes
    $ scripts/run-tests ddtrace/contrib/django/patch.py
    $ scripts/run-tests ddtrace/internal/core/event_hub.py

    # Run test suites containing these tests
    $ scripts/run-tests tests/contrib/django/
    $ scripts/run-tests tests/contrib/flask/test_flask.py

**Manual approach with ddtest**

This repo includes a Docker container definition that provides a pre-built test environment.
You can access it and run repository tools with commands such as:

.. code-block:: bash

    $ scripts/ddtest
    $ scripts/ddtest scripts/lint style


How do I run only the tests I care about?
-----------------------------------------

**Easy way: Use scripts/run-tests**

Pass test-command arguments after ``--``:

.. code-block:: bash

    $ scripts/run-tests tests/contrib/django/ -- -k test_specific_function
    $ scripts/run-tests tests/contrib/flask/ -- -k "test_request or test_response"

List the matching environments before selecting one by hash. The runner starts any services declared by the suite:

.. code-block:: bash

    $ scripts/run-tests --list tests/contrib/django/
    $ scripts/run-tests --venv <environment-hash> -- -k test_specific_function

Matching test environments are reused by default. Pass ``-r`` before ``--`` after changing native code or build
configuration, or after updating from main when those inputs may have changed. This recreates the environment and
refreshes the editable ddtrace build.

.. code-block:: bash

    $ scripts/run-tests -r --venv <environment-hash> -- -k test_specific_function

An ``-s`` after ``--`` still belongs to the test command and disables output capture.
The legacy runner option ``-s`` before ``--`` is a no-op; use ``-r`` to refresh the installation.

Nightly coverage report upload
------------------------------

Nightly test environments enable coverage report upload by default only on Python 3.15. This focuses reports on
new Python support without paying the collection and reporting cost on every Python version. Other nightly
Python versions explicitly disable report upload; suites without a Python 3.15 environment do not fall back to
another version. The full test matrix and the separate ITR coverage policy are unchanged.

The version selection is defined by ``NIGHTLY_COVERAGE_PYTHON_VERSIONS`` in ``tests/suitespec.py``. Add versions
there when expanding nightly report coverage. Explicit matrix, variant, and run environment overrides retain
their existing precedence, including suites that disable report upload. Non-nightly defaults are unchanged.

OpenFeature fixtures
--------------------

The OpenFeature suite loads the checked-in snapshot in
``tests/openfeature/ffe-system-test-data/``. A fresh clone includes the fixtures;
running the tests requires no submodule initialization or fixture download.

Add or change shared evaluation cases in
`DataDog/ffe-system-test-data <https://github.com/DataDog/ffe-system-test-data>`_
first. To refresh the snapshot from a reviewed upstream commit, run:

.. code-block:: bash

    $ python scripts/update-ffe-fixtures.py --ref <upstream-commit>
    $ scripts/run-tests --list tests/openfeature/
    $ scripts/run-tests --venv <environment-hash>

``SOURCE.md`` records the upstream commit. Do not edit the generated fixture files
locally. Only ``ufc-config.json`` and ``evaluation-cases/*.json`` are copied;
unexpected entries inside ``evaluation-cases/`` are rejected.

The ``Check FFE fixtures`` PR check fetches the exact commit recorded in
``SOURCE.md`` and compares the copied filenames and contents. It does not update
the snapshot. To run the same check locally (requires network access):

.. code-block:: bash

    $ python scripts/update-ffe-fixtures.py --check

The weekly or manually dispatched ``Update FFE fixtures`` workflow opens a signed
draft PR when the copied contents change. If an update PR is already open, it
leaves that branch untouched so evaluator fixes added there are preserved. New
cases may expose evaluator bugs; fix those before merging the update, and change
fixture expectations upstream only when the shared expectation is incorrect.

Why are my tests failing with 404 errors?
-----------------------------------------

If your test relies on the ``testagent`` service, you might see it fail with a 404 error.
To fix this:

.. code-block:: bash

    $ scripts/run-tests <test-path>

Why are my Docker tests failing with permission errors on Linux?
-----------------------------------------------------------------

On Linux systems, when running tests with ``scripts/ddtest`` or ``scripts/run-tests``, you may encounter permission errors or file ownership issues. This happens because the container user's ID and group ID must match your local user's IDs.

To fix this, create a ``docker-compose.override.yml`` file in the repository root with the following contents:

.. code-block:: yaml

    services:
      testrunner:
        user: "${UID}:${GID}"

Then, ensure your shell has the ``UID`` and ``GID`` environment variables set:

.. code-block:: bash

    export UID="$(id -u)"
    export GID="$(id -g)"

You can add these exports to your shell profile (e.g., ``.bashrc``, ``.zshrc``) to make them persistent across sessions.

After setting this up, run your tests normally:

.. code-block:: bash

    $ scripts/ddtest
    $ scripts/run-tests

The ``docker-compose.override.yml`` file is git-ignored and won't be committed, so each developer can have their own local configuration.

Build issues when running tests
-------------------------------

If you encounter build failures, CMake errors, or stale native extension issues when running tests:

- **Installing ddtrace locally** (e.g. ``pip install -e .``): See :ref:`build-failures-local-install` for the clean command.
- **Using scripts/ddtest:** The project is mounted from the host, so run ``scripts/clean`` on the host first.
  The container sees the cleaned project on the next run.

Then rebuild the environment so that the ddtrace installation is refreshed:

.. code-block:: bash

    $ scripts/run-tests -r --venv <environment-hash> -- -vv -k test_name

Why is my CI run failing with a message about requirements files?
-----------------------------------------------------------------

Test environments use committed dependency locks. After changing an environment's dependency definition, regenerate
the locks and commit both changes:

.. code-block:: bash

  $ scripts/test-requirements lock <environment-name>

Omit the environment name to generate all missing locks and prune locks that no longer have a corresponding
environment. Lock generation requires a Linux x86-64 host or the Linux x86-64 testrunner image used by CI.
On Apple Silicon, select that image architecture explicitly:

.. code-block:: bash

  $ DOCKER_DEFAULT_PLATFORM=linux/amd64 scripts/ddtest scripts/test-requirements lock <environment-name>

Use ``scripts/test-requirements`` to inspect and maintain locks:

* ``check`` reports missing or obsolete lock files across all environments.
* ``lock [environment-name ...]`` generates missing locks for exact environment names.
* ``lock --upgrade [environment-name ...]`` upgrades existing locks for exact environment names.

Without an environment name, ``lock`` operates on all environments.

Why is my CI run failing with benchmark or Service Level Objective (SLO) threshold breaches?
---------------------------------------------------------------------------------------------

The library includes automated SLO checks that monitor performance thresholds for execution time and memory usage. If your pull request causes these checks to fail, you'll see benchmark test failures in CI indicating that your changes have caused performance to exceed established thresholds.

**If this is expected additional overhead**:

1. **Add a comment to your PR description** explaining why the performance change is expected and necessary

2. **Update the failing thresholds** in ``.gitlab/benchmarks/bp-runner.microbenchmarks.fail-on-breach.yml`` following these guidelines:

   **For execution time thresholds:**

   * Take the new benchmark result from CI
   * Add 2% overhead for variance
   * Round up to a reasonable precision
   * Example: 23.1 ms → 23.1 * 1.02 = 23.562 ms → round to 23.60 ms

   **For memory usage thresholds:**

   * Take the new benchmark result from CI
   * Add 5% overhead for variance
   * Round up to a reasonable precision
   * Consider unifying similar scenarios to the same threshold (e.g., set all ``tracer`` scenarios to ``< 32.00 MB`` instead of having slightly different values)

**Example threshold update:**

.. code-block:: yaml

    - name: span-start
      thresholds:
        - execution_time < 23.60 ms  # was 23.50 ms
        - max_rss_usage < 48.00 MB   # was 47.50 MB

How do I add a new test suite?
------------------------------

Add the suite and its dependency variants to the nearest ``suitespec.yml`` file, then regenerate the dependency
locks. See ``tests/README.md`` for the schema and use ``scripts/run-tests`` for local validation.

How do I update a test environment to use the latest version of a package?
----------------------------------------------------------------------------

Update the dependency constraint in the suite's ``suitespec.yml`` matrix, run
``scripts/test-requirements lock --upgrade <environment-name>``, and commit the definition and resulting lock changes.

Why isn't my lint dependency change taking effect?
--------------------------------------------------

If you update tool versions in the ``[dependency-groups]`` ``lint`` section of ``pyproject.toml``,
uv will pick up the change automatically on the next run. To force a clean reinstall of the lint
environment, clear the uv cache:

.. code-block:: bash

    $ uv cache clean


What do I do when my pull request has failing tests unrelated to my changes?
----------------------------------------------------------------------------

The test suite is not completely reliable. There are usually some tests that can fail without any of their code paths being
changed. This slows down development because most tests are required to pass for pull requests to be merged.

The ``tests/utils`` module provides the ``@flaky`` decorator (`link <https://github.com/DataDog/dd-trace-py/blob/623f2df4de802563a463acc4d3c000dbc742e3d3/tests/utils.py#L1285>`_) to enable contributors to handle this situation. As a contributor,
when you notice a test failure that is unrelated to the changes you've made, you can add the ``@flaky`` decorator to that test.
This will cause the test's result not to count as a failure during pre-merge checks.

The decorator requires as a parameter a UNIX timestamp specifying the time at which the decorator will stop skipping the test.
A timestamp a few months in the future is a fine default to use.

``@flaky`` is intended to be used liberally by contributors to unblock their work. Add it whenever you notice an apparently flaky
test. It is, however, a short-term fix that you should not consider to be a permanent resolution.

Using ``@flaky`` comes with the responsibility of maintaining the test suite's coverage over the library. If you're in the habit
of using it, periodically set aside some time to ``grep -R 'flaky' tests`` and remove some of the decorators. This may require
finding and fixing the root cause of the unreliable behavior. Upholding this responsibility is an important way to keep the test
suite's coverage meaningfully broad while skipping tests.


How do I find out if a slow or timed-out CI job ran on a slow machine?
-----------------------------------------------------------------------

Maintainers can read the resource metrics of the pod that ran a CI job in Datadog.
Use the pod metrics first. They need no change to the pipeline, and they work for a job that already finished.

1. Open the job in GitLab. Note the job id from the URL, the start time, and the end time.
2. In the job log, find the line ``Running on <pod> via <runner>``. Read the ``Waiting for pod``
   lines above it. A reason such as ``Unschedulable`` means that the job waited for a machine,
   not for tests.
3. In Datadog Metrics, query the pod metrics with the ``ci-job-id`` tag and the container name
   ``build``. Use the time from 2 minutes before the job start to 1 minute after the job end.
   Compare the CPU usage with the CPU request and limit. Compare the memory working set with the
   memory limit. Check the OOM event count. Group the query by the ``host`` tag to see the node
   that ran the job.
4. To explain a slow start, query the pod phase, ready, and unschedulable metrics with the pod name.
   These metrics do not carry the job id. Search the Kubernetes events for the same pod name to
   find scheduling failures.

Do not trust the CPU throttling counters. The counters can read zero when a container uses more
CPU than its limit. Compare the CPU usage with the limit instead.

If the pod metrics do not explain the result, turn on the runtime probe. The root ``conftest.py``
contains the probe. When the probe is on, it prints a ``RUNTIME PROBE`` block in the job log at
the start and at the end of each pytest session.
The block shows the CPU count and affinity, the cgroup CPU and memory limits and counters, and
the results of three short benchmarks.
Use the block to compare the machine against the machines of other jobs in the same pipeline.

The probe is off by default because the benchmarks add a fraction of a second to each pytest session.
To turn the probe on for a CI run, start a new pipeline for your branch and set the pipeline
variable ``DD_TEST_RUNTIME_PROBE`` to ``1``.
In GitLab, use **Build > Pipelines > New pipeline** and add the variable in the form.
The ``run-tests-trigger`` job passes the variable to the test jobs in the child pipeline.
A retry of a single job does not accept new variables, so a retry does not turn on the probe.

To turn the probe on for a local run, set the variable before you run the tests::

    $ DD_TEST_RUNTIME_PROBE=1 scripts/run-tests --venv <hash>

``scripts/ddtest`` passes the variable into the test container.

The probe does not run in ``pytest-xdist`` workers.
When the GitLab job timeout or an out-of-memory kill stops a job, pytest does not run its
session-end hook. In that case, the log contains only the block from the session start.


How do I enable debug logs for just a specific part of the library?
-------------------------------------------------------------------

Enabling debug logs for the whole library with ``DD_TRACE_DEBUG=1`` is often too
noisy. Log levels for hierarchies of loggers can be controlled with internal
environment variables. For example, to enable debug logs just for
``ddtrace.debugging``, one can set ```_DD_DEBUGGING_LOG_LEVEL=DEBUG```. This
will set the ``DEBUG`` log level for any logger whose name is prefixed with
``ddtrace.debugging``.
