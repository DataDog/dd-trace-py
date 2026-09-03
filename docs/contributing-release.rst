Release Process
===============

“Release candidates” are releases of dd-trace-py that are tagged with version strings including `rc` and marked in GitHub as “pre-releases”.
Most of the time spent “running the release” is actually spent creating and testing release candidates.
We create a release candidate, test it, and repeat until the test shows no errors or regressions.

Release candidates are based on the main branch. The main branch's version string is always set to the release candidate version that will be released next.

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
2. Make a GitHub release

There is no version-string pull request in any of these steps: the version is computed automatically
from git state at build time by ``scripts/compute_version.py``. See "Version String" below. The only
manual action that determines a release's version is the exact tag created by the GitHub release
itself.

Here are the specifics of those steps for all of the different types of releases. See even more detail in the sections that follow.

Patch Release
-------------

1. Set / Validate branch structure

    * A branch named after the relevant minor version line (``X.Y``) is expected to already exist. This is the "release branch".

2. Make a GitHub release

    * Target: the tip of the release branch
    * "Set as latest" may be applicable
    * "Pre-release" unchecked

Minor or Major Release Candidate
--------------------------------

1. Set / Validate branch structure

    * The main branch exists

2. Make a GitHub release

    * Target: the tip of the main branch
    * "Pre-release" checked

Minor or Major Release
----------------------

1. Set / Validate branch structure

    * There is no branch in existence named after the relevant minor release line (the "release branch"). Create it:

    .. code-block:: bash

        $ git checkout A.B  # previous release branch
        $ git pull
        $ git checkout -b X.Y  # new release branch
        $ git merge main -Xtheirs # this keeps the tags intact so that reno will work properly
        $ git push -u origin X.Y

2. Make a GitHub release

    * Target: the tip of the release branch created above
    * "Set as latest" checked
    * "Pre-release" unchecked

Version String
--------------

ddtrace's version is not a literal anywhere in the source tree. It's computed at build time by
``scripts/compute_version.py`` from git state, and exposed as ``ddtrace.__version__`` via the
installed package's metadata. If you inspect a ddtrace wheel via directory exploration or
``print(ddtrace.__version__)``, the version you find will match the version in the wheel's name.

The rules, applied in order:

1. If the commit being built is exactly tagged ``vX.Y.Z`` or ``vX.Y.ZrcN``, the version is that tag,
   verbatim (``v`` stripped). This is the only way a final or ``rc`` version is ever produced — always
   by a human creating that exact tag, e.g. as part of making a GitHub release.
2. Otherwise, on a release branch (named ``X.Y``): the next patch version after the latest ``vX.Y.Z``
   tag on that branch, suffixed ``.devN`` (``N`` = commits since that tag), or ``X.Y.0.devN`` if the
   branch was just cut and has no tag yet.
3. Otherwise (``main``, feature branches, PRs): the next minor version after the latest final release
   anywhere in the repo, suffixed ``.devN``.

For example:

* Latest release: ``4.4.0`` -> version on main: ``4.5.0.devN``
* Latest release: ``4.3.0rc4`` -> version on main: ``4.4.0.devN`` (release candidates don't count as
  "the latest final release" — only an actual ``vX.Y.Z`` tag does)
* Latest release: ``4.4.0`` -> version on the ``4.4`` branch: ``4.4.1.devN``
* Latest release: ``4.2.1`` -> version on the freshly-cut ``4.3`` branch, no tag yet: ``4.3.0.devN``

Branch identity for rules 2/3 comes from CI-provided ref info (GitHub Actions'
``GITHUB_HEAD_REF``/``GITHUB_REF_NAME``, GitLab's ``CI_COMMIT_BRANCH``/``CI_COMMIT_REF_NAME``), not
from ``git rev-parse --abbrev-ref HEAD`` — that returns the literal string ``"HEAD"`` under a detached
checkout, which is the norm for CI, and silently treats release branches as "main-like" if
relied upon. To compute what a particular branch's version would be from a local, possibly-detached
checkout (for example, testing a release branch cut before pushing it), set
``_DD_TRACE_BUILD_VERSION=X.Y`` in the environment to override branch detection:

.. code-block:: bash

    $ _DD_TRACE_BUILD_VERSION=4.13 scripts/compute_version.py

If you ever find a computed version doesn't match one of the rules above, that's a bug in
``scripts/compute_version.py`` (see ``tests/scripts/test_compute_version.py``) — not something to work
around by hand-editing a version string, since there is no longer one to edit.


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
