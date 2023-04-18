==============
 Contributing
==============

When contributing to this repository, we advise you to discuss the change you
wish to make via an `issue <https://github.com/DataDog/dd-trace-py/issues>`_.


Branches
========

Development happens in the `1.x` branch. When all the features for the next
milestone are merged, the next version is released and tagged on the `1.x`
branch as `vVERSION`.

Your pull request should target the `1.x` branch.

Once a new version is released, a `VERSION` branch might be created to
support micro releases to `VERSION`. Patches should be cherry-picking from the
`1.x` branch where possible — or otherwise created from scratch.


Internal API
============

The `ddtrace.internal` module contains code that must only be used inside
`ddtrace` itself. Relying on the API of this module is dangerous and can break
at anytime. Don't do it.

Python Versions and Implementations Support
===========================================

The following Python implementations are supported:

- CPython

Versions of those implementations that are supported are the Python versions
that are currently supported by the community.

Code Style
==========

The code style is enforced by `flake8 <https://pypi.org/project/flake8>`_, its
configuration, and possibly extensions. No code style review should be done by
a human. All code style enforcement must be automated to avoid bikeshedding
and losing time.


Logging
=======

The ddtrace logger should be used to log events in the dd-trace-py library. Use ``ddtrace.internal.logger.get_logger(__name__)`` to initialize/retrieve an instance of the ddtrace logger (DDLogger).

To ensure the ddtrace library produces consistent and secure logs the following best practices should be followed:

* Logs should be generated with the level DEBUG, INFO, WARNING, or ERROR.
* Log messages are grammatically correct and do not contain spelling errors.
* Log messages should be standalone and actionable. They should not require context from other logs, metrics or trace data.
* Log data is sensitive and should not contain application secrets or other sensitive data.

Changing Test Requirements
==========================

`.riot/requirements` contains requirements files generated with `pip-compile` for every environment specified by `riotfile.py`.
Riot uses these files to build its environments, and they do not get rebuilt automatically when the riotfile changes.
Thus, if you make changes to the riotfile, you need to run either `scripts/compile-and-prune-test-requirements` or `riot run -c <mytests>`
to regenerate the requirements files for the environments that changed. You can commit and pull request changes to files in `.riot/requirements`
alongside the corresponding changes to `riotfile.py`.


.. toctree::
    :hidden:

    contributing-integrations
    releasenotes

Pre-commit Hooks
================

The tracer library uses formatting/linting tools including black, flake8, and mypy.
While these are run in each CI pipeline for pull requests, they are also **optionally** available to be automated to run
when you call git commit as pre-commit hooks to catch any formatting errors before you commit.
To initialize the pre-commit hook script to run in your development branch, run the following command:

    $ rm .git/hooks/pre-commit

