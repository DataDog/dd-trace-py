==============
 Contributing
==============

Contributions are welcome!

The best way to suggest a change to the library is to open a
`pull request <https://github.com/DataDog/dd-trace-py/pulls>`_.

You may also consider opening an `issue <https://github.com/DataDog/dd-trace-py/issues>`_
if you'd like to report a bug or request a new feature.

Before working on the library, install `docker <https://www.docker.com/products/docker>`_.

If you're trying to set up a local development environment, read `this <https://github.com/DataDog/dd-trace-py/tree/main/docs/contributing-testing.rst>`_.

`Library design documentation for contributors <https://github.com/DataDog/dd-trace-py/tree/main/docs/contributing-design.rst>`_.

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

Instrumentation Telemetry
-------------------------

When you implement a new feature in ddtrace, you should usually have the library emit some Instrumentation
Telemetry about the feature. Instrumentation Telemetry provides data about the operation of the library in
real production environments and is often used to understand rates of product adoption.

Instrumentation Telemetry conforms to an internal Datadog API. You can find the API specification in the
private Confluence space. To send Instrumentation Telemetry data to this API from ddtrace, you can use
the ``ddtrace.internal.telemetry.telemetry_writer`` object that provides a Python interface to the API.

The most common ``telemetry_writer`` call you may use is ``add_count_metric``. This call generates timeseries
metrics that you can use to, for example, count the number of times a given feature is used. Another useful
call is ``add_integration``, which generates telemetry data about the integration with a particular module.

Read the docstrings in ``ddtrace/internal/telemetry/writer.py`` for more comprehensive usage information
about Instrumentation Telemetry.

.. toctree::
    :hidden:

    contributing-design
    contributing-integrations
    contributing-testing
    contributing-tracing
    contributing-release
    releasenotes
