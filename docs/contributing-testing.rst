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

We assume you have `docker <https://www.docker.com/products/docker>`_ installed.

In addition, you will need `riot <https://ddriot.readthedocs.io/en/latest/>`_ and `hatch <https://hatch.pypa.io/latest/>`_.

.. code-block:: bash

    $ pip install riot==0.19.1
    $ pip install hatch~=1.8.0 hatch-containers==0.7.0

Some of our test environments are managed with Riot, others with Hatch.

For riot environments, you can run:

.. code-block:: bash

    $ scripts/ddtest riot run -p 3.10

This command runs the entire test suite, which is probably not what you want to do.

For hatch environments, you can run:

.. code-block:: bash

    $ hatch run lint:style

If you make a change to the `hatch.toml` or library dependencies, be sure to remove environments before re-running:

.. code-block:: bash

    $ hatch env remove <ENV> # or hatch env prune


How do I run only the tests I care about?
-----------------------------------------

1. Note the names of the tests you care about - these are the "test names".
2. Find the ``Venv`` in the `riotfile <https://github.com/DataDog/dd-trace-py/blob/32b88eadc00e05cd0bc2aec587f565cc89f71229/riotfile.py#L426>`_
   whose ``command`` contains the tests you're interested in. Note the ``Venv``'s ``name`` - this is the
   "suite name".
3. Find the directive in the `CI config <https://github.com/DataDog/dd-trace-py/blob/32b88eadc00e05cd0bc2aec587f565cc89f71229/.circleci/config.yml#L664>`_
   whose ``pattern`` is equal to the suite name. Note the ``docker_services`` section of the directive, if present -
   these are the "suite services".
4. Start the suite services, if applicable, with ``$ docker-compose up -d service1 service2``.
5. Start the test-runner Docker container with ``$ scripts/ddtest``.
6. In the test-runner shell, run the tests with ``$ riot -v run --pass-env -p 3.10 <suite_name> -- -s -vv -k 'test_name1 or test_name2'``.

Anatomy of a Riot Command
-------------------------

.. code-block:: bash

    $ riot -v run --pass-env -s -p 3.10 <suite_name> -- -s -vv -k 'test_name1 or test_name2'

* ``-v``: Print verbose output
* ``--pass-env``: Pass all environment variables in the current shell to the pytest invocation
* ``-s``: Skips base install. Ensure you have already generated the base virtual environment(s) before using this flag.
* ``-p 3.10``: Run the tests using Python 3.10. You can change the version string if you want.
* ``<suite_name>``: A regex matching the names of the Riot ``Venv`` instances to run
* ``--``: Everything after this gets treated as a ``pytest`` argument
* ``-s``: Make potential uses of ``pdb`` work properly
* ``-vv``: Be loud about which tests are being run
* ``-k 'test1 or test2'``: Test selection by `keyword expression <https://docs.pytest.org/en/7.1.x/how-to/usage.html#specifying-which-tests-to-run>`_

Why are my tests failing with 404 errors?
-----------------------------------------

If your test relies on the ``testagent`` service, you might see it fail with a 404 error.
To fix this:

.. code-block:: bash

    # outside of the testrunner shell
    $ docker-compose up -d testagent

    # inside the testrunner shell, started with scripts/ddtest
    $ DD_AGENT_PORT=9126 riot -v run --pass-env ...

Why is my CI run failing with a message about requirements files?
-----------------------------------------------------------------

``.riot/requirements`` contains requirements files generated with ``pip-compile`` for every environment specified
by ``riotfile.py``. Riot uses these files to build its environments, and they do not get rebuilt automatically
when the riotfile changes. Thus, if you make changes to the riotfile, you need to rebuild them.

.. code-block:: bash

  $ scripts/ddtest scripts/compile-and-prune-test-requirements

You can commit and pull request the resulting changes to files in ``.riot/requirements`` alongside the
changes you made to ``riotfile.py``.

How do I add a new test suite?
------------------------------

We use `riot <https://ddriot.readthedocs.io/en/latest/>`_, a Python virtual environment constructor, to run the test suites.
It is necessary to create a new ``Venv`` instance in ``riotfile.py`` if it does not exist already. It can look like this:

.. code-block:: python

    Venv(
        name="asyncio",
        command="pytest {cmdargs} tests/contrib/asyncio",
        pys=select_pys(),
        pkgs={
            "pytest-asyncio": latest,
        },
        env={
            "DD_ENV_VARIABLE": "1",  # if needed
        },
    )

Once a ``Venv`` instance has been created, you will be able to run it as explained in the section below.
Next, we will need to add a new CircleCI job to run the newly added test suite at ``.circleci/config.templ.yml`` just like:

.. code-block:: python

    asyncio:
    <<: *contrib_job
    steps:
      - run_test:
          pattern: 'asyncio'


After this, a new component must be added to ``tests/.suitespec.json`` under ``"components":`` like:

.. code-block:: JSON

    "asyncio": [
        "ddtrace/contrib/asyncio/*"
    ],

Lastly, we will register it as a suite in the same file under ``"suites":``:

.. code-block:: JSON

    "asyncio": [
        "@asyncio",
        "tests/contrib/asyncio/*"
    ],

Once you've completed these steps, CircleCI will run the new test suite.

How do I update a Riot environment to use the latest version of a package?
--------------------------------------------------------------------------

Reading through the above example and others in ``riotfile.py``, you may notice that some package versions are specified
as the variable ``latest``. When the Riotfile is compiled into the ``.txt`` files in the ``.riot`` directory, ``latest`` tells
the compiler to pin the newest version of the package available on PyPI according to semantic versioning.

Because this version resolution happens during Riotfile compilation, ``latest`` doesn't always mean "latest" once the compiled
requirements files are checked into source control. In order to stay current, these requirements files need to be recompiled
periodically.

Assume you have a ``Venv`` instance in the Riotfile that uses the ``latest`` variable. Note the ``name`` field of this
environment object.

1. Run ``scripts/ddtest`` to enter a shell in the testrunner container
2. ``export VENV_NAME=<name_you_noted_above>``
3. Delete all of the requirements lockfiles for the chosen environment, then regenerate them:
   ``for h in `riot list --hash-only "^${VENV_NAME}$"`; do rm .riot/requirements/${h}.txt; done; scripts/compile-and-prune-test-requirements``
4. Commit the resulting changes to the ``.riot`` directory, and open a pull request against the trunk branch.

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
