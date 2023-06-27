==============
 Contributing
==============

Contributions are welcome!

The best way to suggest a change to the library is to open a
`pull request <https://github.com/DataDog/dd-trace-py/pulls>`_.

You may also consider opening an `issue <https://github.com/DataDog/dd-trace-py/issues>`_
if you'd like to report a bug or request a new feature.

Thanks for working with us!

Branches and Pull Requests
==========================

This library follows the practice of `trunk-based development <https://trunkbaseddevelopment.com/>`_.

The "trunk" branch, which new pull requests should target, is `1.x`.
Roughly every two weeks, we checkpoint the current state of this branch as a new
release branch, whose naming follows `semantic versioning <https://semver.org/>`_.
You can find the list of past released versions `here <https://github.com/DataDog/dd-trace-py/releases>`_.

Pull requests are named according to the `conventional commit <https://www.conventionalcommits.org/en/v1.0.0/>`_
standard, which is enforced by a continuous integration job. The standardized "scopes" we use
in pull request names are enumerated `here <releasenotes.rst#Scope>`_.

Backporting
===========

Each minor version has its own branch. Bug fixes are "backported" from trunk to certain
minor version branches according to the `version support policy <versioning.rst#release-versions>`_.

* **Fix PRs** are backported to all maintained release branches.
* **CI PRs** are backported to the maintained release branches.
* **New features** (`feat` PRs) are not backported.
* **Chore, documentation, and other PRs** are not backported.

Change Process
==============

code reviews, requesting
seeing if CI passes

Code style is automatically checked by various tools including Flake8, Black, and MyPy. This
means that code reviews don't need to worry about style and can focus on substance.

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
to regenerate the requirements files for the environments that changed. In order to run the script, you need to have all minor versions of Python that the tracer supports.
The easiest way to accomplish this and generate the files is to simply spin up the testagent container, exec into it, and run the script from there.

.. code-block:: bash

  cd dd-trace-py && docker run --network host --userns=host --rm -w /root/project -v $PWD/:/root/project \
    -it ghcr.io/datadog/dd-trace-py/testrunner:1ed971833a2a3c97f43cbaeabcbb3f1e28745a00 \
    bash -c "git config --global --add safe.directory /root/project && pip install riot && bash -i './scripts/compile-and-prune-test-requirements'"


This can also be accomplished using pyenv and installing all of the Python versions before running the script.
You can commit and pull request changes to files in `.riot/requirements` alongside the corresponding changes to `riotfile.py`.

.. toctree::
    :hidden:

    contributing-integrations
    releasenotes
