==============
 Contributing
==============

Contributions are welcome!

The best way to suggest a change to the library is to open a
`pull request <https://github.com/DataDog/dd-trace-py/pulls>`_.

You may also consider opening an `issue <https://github.com/DataDog/dd-trace-py/issues>`_
if you'd like to report a bug or request a new feature.

Before working on the library, install `docker <https://www.docker.com/products/docker>`_.

Thanks for working with us!

.. _change_process:

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

If you get errors from ``git commit`` that mention "pre-commit", run ``$ rm .git/hooks/pre-commit`` and try again.

Branches and Pull Requests
--------------------------

This library follows the practice of `trunk-based development <https://trunkbaseddevelopment.com/>`_.

The "trunk" branch, which new pull requests should target, is ``1.x``.
Roughly every two weeks, we checkpoint the current state of this branch as a new
release branch, whose naming follows `semantic versioning <https://semver.org/>`_.
You can find the list of past released versions `on this GitHub page <https://github.com/DataDog/dd-trace-py/releases>`_.

Pull requests are named according to the `conventional commit <https://www.conventionalcommits.org/en/v1.0.0/>`_
standard, which is enforced by a continuous integration job. The standardized "scopes" we use
in pull request names are enumerated :ref:`in the release notes documentation<release_notes_scope>`.

Pull requests that change the library's public API require a :ref:`release note<release_notes>`.
If your pull request doesn't change the public API, apply the ``no-changelog`` label.

Once approved, pull requests should be merged with the "Squash and Merge" option.
At this time, do not use the merge queue option.

Backporting
-----------

Each minor version has its own branch.

* **Fix PRs** are backported to all maintained release branches.
* **CI PRs** are backported to the maintained release branches.
* **New features** (``feat`` PRs) are not backported.
* **Chore, documentation, and other PRs** are not backported.

If your pull request is a ``fix`` or ``ci`` change, apply the backport labels corresponding to the minor
versions that need the change.

Commit Hooks
------------

The tracer library uses formatting/linting tools including black, flake8, and mypy.
While these are run in each CI pipeline for pull requests, they are automated to run
when you call `git commit` as pre-commit hooks to catch any formatting errors before
you commit.

To initialize the pre-commit hook script to run in your development
branch, run ``$ hooks/autohook.sh install``.

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
separate. It is implemented in the Python files in the `top level of ddtracepy <https://github.com/DataDog/dd-trace-py/tree/main/ddtrace>`_
and in the `internal` module. As an implementation detail, the core logic also happens to directly support
`Application Performance Monitoring <https://docs.datadoghq.com/tracing/>`_.

Be mindful and intentional about which of these categories your change fits into, and avoid mixing concerns between
categories. If doing so requires more foundational refactoring or additional layers of abstraction, consider
opening an issue describing the limitations of the current design.

Tests
-----

If your change touches Python code, it should probably include at least one test. See the
`testing guidelines <https://github.com/DataDog/dd-trace-py/tree/main/docs/contributing-testing.rst>`_ for details.

Releases
--------

If you're managing a new release of the library, follow the instructions
`here <https://github.com/DataDog/dd-trace-py/tree/main/docs/contributing-release.rst>`_.

Documentation
-------------

Pull requests implementing new features should include documentation for those features. The audience for this
documentation is the public population of library users. The Products and Core logic are documented alongside
this document, in the ``docs`` directory. The documentation for each Integration is contained in a docstring
in that integration's ``__init__.py`` file. See :ref:`this page<integration_guidelines>` for more information
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
    contributing-testing
    contributing-tracing
    contributing-release
    releasenotes
