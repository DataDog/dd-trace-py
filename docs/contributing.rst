==============
 Contributing
==============

Contributions are welcome!

The best way to suggest a change to the library is to open a
`pull request <https://github.com/DataDog/dd-trace-py/pulls>`_.

You may also consider opening an `issue <https://github.com/DataDog/dd-trace-py/issues>`_
if you'd like to report a bug or request a new feature.

Thanks for working with us!

Change Process
==============

The process of making a change to the library starts with a pull request. When you open one,
reviewers are automatically assigned based on the CODEOWNERS file. Many different continuous integration
jobs are also triggered, including unit tests, integration tests, benchmarks, and linters.

Marking your pull request as a draft using the "Convert to draft" link communicates to potential reviewers
that your pull request is not ready to be reviewed. Use the "ready for review" button when this changes.
It's often beneficial to open an in-progress pull request and mark it as a draft while you confirm that the test
suite passes.

Within a few business days, one of the maintainers will respond with a code review. The review will
primarily focus on idiomatic Python usage, efficiency, testing, and adherence to the versioning policy.
Correctness and code style are automatically checked in continuous integration, with style linting managed by
various tools including Flake8, Black, and MyPy. This means that code reviews don't need to worry about style
and can focus on substance.

Branches and Pull Requests
--------------------------

This library follows the practice of `trunk-based development <https://trunkbaseddevelopment.com/>`_.

The "trunk" branch, which new pull requests should target, is ``1.x``.
Roughly every two weeks, we checkpoint the current state of this branch as a new
release branch, whose naming follows `semantic versioning <https://semver.org/>`_.
You can find the list of past released versions `on this GitHub page <https://github.com/DataDog/dd-trace-py/releases>`_.

Pull requests are named according to the `conventional commit <https://www.conventionalcommits.org/en/v1.0.0/>`_
standard, which is enforced by a continuous integration job. The standardized "scopes" we use
in pull request names are enumerated `in the release notes documentation <releasenotes.rst#Scope>`_.

Pull requests that change the library's public API require a `release note <releasenotes.rst>`_.
If your pull request doesn't change the public API, apply the ``no-changelog`` label.

Backporting
-----------

Each minor version has its own branch. Bug fixes are "backported" from trunk to certain
minor version branches according to the `version support policy <versioning.rst#release-versions>`_.

* **Fix PRs** are backported to all maintained release branches.
* **CI PRs** are backported to the maintained release branches.
* **New features** (``feat`` PRs) are not backported.
* **Chore, documentation, and other PRs** are not backported.

If your pull request is a ``fix`` or ``ci`` change, apply the backport labels corresponding to the minor
versions that need the change.

Implementation Guidelines
=========================

Parts of the Library
--------------------

When designing a change, one of the first decisions to make is where it should be made. This is an overview
of the main functional areas of the library.

A **product** is a unit of code within the library that implements functionality specific to a small set of
customer-facing Datadog products. Examples include the `appsec module <https://github.com/DataDog/dd-trace-py/tree/1.x/ddtrace/appsec>`_
implementing functionality for `Application Security Management <https://www.datadoghq.com/product/application-security-management/>`_
and the `profiling <https://github.com/DataDog/dd-trace-py/tree/1.x/ddtrace/profiling>`_ module implementing
functionality for `Continuous Profiling <https://docs.datadoghq.com/profiler/>`_. Ideally it only contains code
that is specific to the Datadog product being supported, and no code related to Integrations.

An **integration** is one of the modules in the `contrib <https://github.com/DataDog/dd-trace-py/tree/f26a526a6f79870e6e6a21d281f4796a434616bb/ddtrace/contrib>`_
directory, hooking our code into the internal logic of a given Python library. Ideally it only contains code
that is specific to the library being integrated with, and no code related to Products.

The **core** of the library is the abstraction layer that allows Products and Integrations to keep their concerns
separate. It is implemented in the Python files in the `top level of ddtracepy <https://github.com/DataDog/dd-trace-py/tree/1.x/ddtrace>`_
and in the `internal` module. As an implementation detail, the core logic also happens to directly support
`Application Performance Monitoring <https://docs.datadoghq.com/tracing/>`_.

Be mindful and intentional about which of these categories your change fits into, and avoid mixing concerns between
categories. If doing so requires more foundational refactoring or additional layers of abstraction, consider
opening an issue describing the limitations of the current design.

Tests
-----

If your change touches Python code, it should probably include at least one test. We use heuristics to
decide when and what sort of tests to write. For example, a pull request implementing a new feature
should include enough unit tests to cover the feature's "happy path" use cases in addition to any known
likely edge cases. If the feature involves a new form of communication with another component (like the
Datadog Agent or libddwaf), it should probably include at least one integration test exercising the end-to-end
communication.

If a pull request fixes a bug, it should include a test that, on the trunk branch, would replicate the bug.
Seeing this test pass on the fix branch gives us confidence that the bug was actually fixed.

Put your code's tests in the appropriate subdirectory of the ``tests`` directory based on what they are testing.
If your feature is substantially new, you may decide to create a new ``tests`` subdirectory in the interest
of code organization.

``.riot/requirements`` contains requirements files generated with ``pip-compile`` for every environment specified
by ``riotfile.py``. Riot uses these files to build its environments, and they do not get rebuilt automatically
when the riotfile changes. Thus, if you make changes to the riotfile, you need to run either
``scripts/compile-and-prune-test-requirements`` or ``riot run -c <mytests>`` to regenerate the requirements
files for the environments that changed. In order to run the script, you need to have all minor versions
of Python that the tracer supports. The easiest way to accomplish this and generate the files is to simply
spin up the testagent container, exec into it, and run the script from there.

.. code-block:: bash

  cd dd-trace-py && docker run --network host --userns=host --rm -w /root/project -v $PWD/:/root/project \
    -it ghcr.io/datadog/dd-trace-py/testrunner \
    bash -c "git config --global --add safe.directory /root/project && pip install riot && bash -i './scripts/compile-and-prune-test-requirements'"


This can also be accomplished using pyenv and installing all of the Python versions before running the script.
You can commit and pull request changes to files in ``.riot/requirements`` alongside the corresponding changes to ``riotfile.py``.

Documentation
-------------

Pull requests implementing new features should include documentation for those features. The audience for this
documentation is the public population of library users. The Products and Core logic are documented alongside
this document, in the ``docs`` directory. The documentation for each Integration is contained in a docstring
in that integration's ``__init__.py`` file. See `this page <contributing-integrations.rst>`_ for more information
on writing documentation for Integrations.

Logging
-------

Use ``ddtrace.internal.logger.get_logger(__name__)`` to initialize/retrieve a ``DDLogger`` object you can use
to emit well-formatted log messages from your code.

Keep the following in mind when writing logging code:

* Logs should be generated with the level ``DEBUG``, ``INFO``, ``WARNING``, or ``ERROR`` according to conventions
  `like these <https://stackoverflow.com/a/2031209/735204>`_.
* Log messages should be grammatically correct and should not contain spelling errors.
* Log messages should be standalone and actionable. They should not require context from other logs, metrics or trace data.
* Log data is sensitive and should not contain application secrets or other sensitive data.


.. toctree::
    :hidden:

    contributing-integrations
    releasenotes
