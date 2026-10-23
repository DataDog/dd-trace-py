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

On Linux, Docker bind mounts keep the host's file ownership, so the container user must be able to write to
your checkout. The test image runs as ``bits`` (uid 1000). When your host uid is not 1000, ``scripts/ddtest``
(and therefore ``scripts/run-tests``) automatically runs the container command with your host uid and gid instead.

To choose the ids explicitly, set ``DD_TEST_USER``:

.. code-block:: bash

    $ DD_TEST_USER=1234:1234 scripts/ddtest

Set ``DD_TEST_USER`` to an empty value to keep the image's default ``bits`` user:

.. code-block:: bash

    $ DD_TEST_USER= scripts/ddtest

If you previously added a ``docker-compose.override.yml`` that sets ``user: "${UID}:${GID}"`` for the
``testrunner`` service, you can delete it. ``scripts/ddtest`` now handles this itself, and that override does
not work with the current image, because the image's home directory, which holds the Python and Rust
toolchains, is not accessible to other users.

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
