Release Process
===============

“Release candidates” are releases of dd-trace-py that are tagged with version strings including `rc` and marked in GitHub as “pre-releases”.
Most of the time spent “running the release” is actually spent creating and testing release candidates.
We create a release candidate, test it, and repeat until the test shows no errors or regressions.

The first release candidate (``rc1``) of a minor line is based on the main branch. After it is tagged, a release branch named ``X.Y`` is
created from it, and every later release candidate and the final release are cut from that branch.
The main branch's version string is ``X.Y.0rc1`` for the minor line under development, and moves to the next line's ``rc1``
when ``X.Y.0`` is released.

Prerequisites
-------------

1. Figure out the version of the library that you’re working on releasing.

2. Update the pinned system-tests version with `scripts/update-system-tests-version.py` and commit the result

3. Ensure the CI is green on the branch on which the release will be based.

4. Ensure there are no SLO breaches on the release branch (``main`` for new major/minor, ``major.minor`` branch for patch releases). See section below for details.

High-Level Steps
----------------

These are the high-level steps involved in all ddtrace releases:

1. Set / Validate branch structure
2. Set / Validate version strings on branches
3. Make a GitHub release

Here are the specifics of those steps for all of the different types of releases. See even more detail in the sections that follow.

Patch Release
-------------

1. Set / Validate branch structure

    * A branch named after the relevant minor version line (``X.Y``) is expected to already exist. This is the "release branch".

2. Set / Validate version strings on branches

    * The version string on the release branch is currently set to a patch version on the relevant minor release line (for example, ``1.2.3``)
    * Pull request a change to the version string on the release branch that sets it to the patch version you're releasing.

3. Make a GitHub release

    * Target: the merged commit from the version string pull request above
    * "Set as latest" may be applicable
    * "Pre-release" unchecked

Minor or Major Release Candidate
--------------------------------

First release candidate (``rc1``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Set / Validate version strings on branches

    * main's version string should already be ``X.Y.0rc1``.

2. Make a GitHub release

    * Target: ``main``
    * "Pre-release" checked

3. Create the release branch from the ``rc1`` tag:

    .. code-block:: bash

        $ git fetch origin --tags
        $ git checkout -b X.Y vX.Y.0rc1
        $ git push -u origin X.Y

    From this point on, fixes for the release are merged to main and backported to ``X.Y``.

Later release candidates (``rc2`` and up)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Set / Validate branch structure

    * The ``X.Y`` release branch exists, and the backports you want in this release candidate are merged into it.

2. Set / Validate version strings on branches

    * Pull request a change to the version string on the ``X.Y`` branch that increments the ``rcN`` number by one
        (for example, ``4.17.0rc1`` to ``4.17.0rc2``).

3. Make a GitHub release

    * Target: ``X.Y``
    * "Pre-release" checked

Minor or Major Release
----------------------

1. Set / Validate branch structure

    * The ``X.Y`` release branch already exists; it was created after ``rc1`` (see above).

2. Set / Validate version strings on branches

    * The version string on the release branch is the latest release candidate, for example ``1.3.0rc3``.
    * Pull request a change to the version string on the release branch that sets it to the minor version you're releasing,
        for example ``1.3.0``.
    * Pull request a change to the version string on the main branch that sets it to ``rc1`` on the next release line,
        for example ``1.4.0rc1``.

3. Make a GitHub release

    * Target: the merged commit on the release branch from the version string pull request above
    * "Set as latest" checked
    * "Pre-release" unchecked

Version String
--------------

The ``[project.version]`` attribute in ``pyproject.toml`` is the source of truth about the version of ddtrace.
If you inspect a ddtrace wheel via directory exploration or ``print(ddtrace.__version__)``, the version you find will match the
version in the wheel's name.

If you check the attribute on a checkout of the ddtrace main branch, you will find it set to ``rc1`` of the
currently under-development minor release line. It stays there until that line's final release ships. For example:

* Latest release: ``4.4.0`` -> version string on main: ``4.5.0rc1``
* Latest release: ``4.3.0rc4`` -> version string on main: ``4.3.0rc1``
* Latest release: ``4.2.1`` -> version string on main: ``4.3.0rc1``

If you check the attribute on a checkout of a ddtrace release branch with a name like ``X.Y``, you will find it set to the latest
release candidate while the line is in pre-release, and to the patch release version that comes next once it is released. For example:

* Latest release: ``4.4.0`` -> version string on 4.4 branch: ``4.4.1dev``
* Latest release: ``4.3.0rc4`` -> version string on 4.3 branch: ``4.3.0rc4`` (bumped to ``4.3.0rc5`` when the next release candidate is cut)
* Latest release: ``4.2.1`` -> version string on 4.2: ``4.2.2dev``

If ever you discover that one of these guarantees is not upheld, please open a pull request adjusting the version string accordingly.


Pre-Release Performance Gates
-----------------------------

This repository is using pre-release performance quality gates.

On ``main`` or the ``major.minor`` release branch, verify that the latest CI pipeline passed the ``check-slo-breaches`` job.
If any SLO is breached, the release pipeline on GitLab will be blocked.
See our thresholds file(s) at `bp-runner.macrobenchmarks.fail-on-breach.yml <https://github.com/DataDog/dd-trace-py/blob/3cf3342a005c1ef9e345d2a82a631bc827c8617a/.gitlab/benchmarks/bp-runner.macrobenchmarks.fail-on-breach.yml>`_ and `bp-runner.microbenchmarks.fail-on-breach.yml <https://github.com/DataDog/dd-trace-py/blob/3cf3342a005c1ef9e345d2a82a631bc827c8617a/.gitlab/benchmarks/bp-runner.microbenchmarks.fail-on-breach.yml>`_.

There are a few ways to resolve this and unblock the release.

**Prerequisite**

Find the change(s) that contributed the most to performance regression.
You can check from the `Benchmarking Platform - Benchmarks tab <https://benchmarking.us1.prod.dog/benchmarks?projectId=3&ciJobDateStart=1753290587498&ciJobDateEnd=1753895387498&gitBranch=main>`_ and filter by project and branch to see these commits.
Notify the authors in `#apm-python-release <https://dd.enterprise.slack.com/archives/C04MK6NNDG9>`_ to see if there are any easy fixes (less than a day of work) that can be pushed to the release branch.

1. **Merge a fix to resolve the performance regression.**
   This should be considered first, and owned by the author(s) for the change(s) that introduced significant performance regression(s).
2. **Revert the change(s) that contributed the most to performance regression.**
   This should be considered if the regression is not acceptable, but the fix will take longer than a day to merge to the release branch.
3. **Bump the SLO(s) to accommodate for the regressions.**
   This should only be considered if the regressions are reasonable for the change(s) introduced (ex - new feature with expected overhead, crash fixes, major security issues, etc.).
   When updating the SLO thresholds, authors must add a comment to their PR justifying the trade offs.
   See `Performance quality gates - User Guide <https://datadoghq.atlassian.net/wiki/spaces/APMINT/pages/5158175217/Performance+quality+gates+-+User+Guide>`_ for more details.


Generating Release Notes
------------------------

Generate release notes from the relevant branch, usually the x.y release branch.

.. code-block:: bash

    $ git checkout <branch>
    $ git fetch
    $ reno report --branch=origin/<branch> | pandoc -f rst -t gfm --wrap=none | less

The relevant portion of these notes is at the top under the "unreleased" section.

Include an estimated end-of-life block at the top of the new release notes:

.. code-block::

    Estimated end-of-life date, accurate to within three months: MM-YYYY
    See [the support level definitions](https://docs.datadoghq.com/tracing/trace_collection/compatibility/python/#releases) for more information.

Where the EOL month is calculated thus: <this major release line's start month> + <18 months>. In most cases you can simply
copy-paste this block from the previous release on the same major line.


Making a New GitHub Release
---------------------------

1. Draft a new GitHub release https://github.com/DataDog/dd-trace-py/releases/new

2. Set the target commit on the GitHub release draft. Most of the time the current HEAD of the release branch is the appropriate target.
   For minor releases where the version ends with .0, the target commit must exactly match the commit of the latest relevant release candidate.

3. Follow the Release Notes instructions below and paste the result into the release’s description.

4. Make sure the “Set as pre-release" box is CHECKED if publishing a release candidate.
   Make sure the “Set as latest release" box is CHECKED only if publishing a new minor release or a patch release for the latest minor version.
   Click “save draft”.

5. Share the link to the GitHub draft release with someone who can confirm it's correct

6. Click the the green “Publish release” button on the draft release. Double check that you have the correct check boxes checked and unchecked
    based on the release you’re about to publish. Wait for build and publish to succeed.
    The GitHub release will trigger the GitLab workflow that builds wheels and publishes to PyPI.
