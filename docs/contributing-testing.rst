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

After a successful first run, pass ``-s`` before ``--`` to reuse the current ddtrace installation. Omit it after
changing native code or project metadata, or after updating from main.

.. code-block:: bash

    $ scripts/run-tests -s --venv <environment-hash> -- -k test_specific_function

An ``-s`` after ``--`` belongs to the test command and disables output capture.

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

Then run the environment without ``-s`` so that the ddtrace installation is refreshed:

.. code-block:: bash

    $ scripts/run-tests --venv <environment-hash> -- -vv -k test_name

Once the build succeeds, you can use ``-s`` again for faster subsequent runs.

Why is my CI run failing with a message about requirements files?
-----------------------------------------------------------------

Test environments use committed dependency locks. After changing an environment's dependency definition, regenerate
the locks and commit both changes:

.. code-block:: bash

  $ scripts/ddtest scripts/compile-and-prune-test-requirements

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

Test environments are defined in ``riotfile.py``. Add a ``Venv`` alongside a similar suite with its command,
supported Python versions, and oldest/latest dependency constraints. Add the corresponding CI suite to the nearest
``suitespec.yml`` file; see ``tests/README.md`` for that schema. Then regenerate the dependency locks and use
``scripts/run-tests`` for local validation.

.. code-block:: python

    Venv(
        name="your_integration",
        command="pytest {cmdargs} tests/contrib/your_integration",
        venvs=[
            Venv(
                pys=select_pys(min_version="3.9"),
                pkgs={"your-dependency": ["~=1.0", latest]},
            ),
        ],
    )

Next, add the CI suite to ``tests/contrib/suitespec.yml``. The suite's ``pattern`` selects the matching test
environment, while ``paths`` determines which changes schedule the CI job:

.. code-block:: yaml

    components:
      your_integration:
        - ddtrace/contrib/internal/your_integration/*

    suites:
      your_integration:
        pattern: ^your_integration$
        paths:
          - '@bootstrap'
          - '@core'
          - '@contrib'
          - '@tracing'
          - '@your_integration'
          - tests/contrib/your_integration/*

Add fields such as ``services``, ``env``, ``snapshot``, and ``parallelism`` when the suite needs them. See
``tests/README.md`` for the complete schema and nearby suites for working examples.

How do I update a test environment to use the latest version of a package?
----------------------------------------------------------------------------

Update the dependency constraint in the suite's ``Venv`` in ``riotfile.py``, run
``scripts/ddtest scripts/compile-and-prune-test-requirements``, and commit both ``riotfile.py`` and the resulting
``.riot/requirements`` lock changes.

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


How do I enable debug logs for just a specific part of the library?
-------------------------------------------------------------------

Enabling debug logs for the whole library with ``DD_TRACE_DEBUG=1`` is often too
noisy. Log levels for hierarchies of loggers can be controlled with internal
environment variables. For example, to enable debug logs just for
``ddtrace.debugging``, one can set ```_DD_DEBUGGING_LOG_LEVEL=DEBUG```. This
will set the ``DEBUG`` log level for any logger whose name is prefixed with
``ddtrace.debugging``.
