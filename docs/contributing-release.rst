Release Process
===============

“Release candidates” are releases of dd-trace-py that are tagged with version strings including `rc` and marked in GitHub as “pre-releases”.
Most of the time spent “running the release” is actually spent creating and testing release candidates.
We create a release candidate, test it, and repeat until the test shows no errors or regressions.

Release candidates are based on the main branch.

The procedure described here can also be used to create patch releases (those that increment the patch version number in the major.minor.patch SemVer framework).

Prerequisites
-------------

1. Figure out the version of the library that you’re working on releasing.

2. Ensure the CI is green on the branch on which the release will be based.

3. Ensure the SLOs have not been breached on the release branch (`main` for new major/minor, `major.minor` branch for patch releases). See section below for details.

Pre-Release Performance Gates
-----------------------------

This repository is using pre-release performance quality gates.

On `main` or the `major.minor` release branch, verify that the latest CI pipeline passed the `check-slo-breaches` job.

If any SLO is breached, the release pipeline on GitLab will be blocked.

See our thresholds file(s) at `bp-runner.macrobenchmarks.fail-on-breach.yml <https://github.com/DataDog/dd-trace-py/blob/3cf3342a005c1ef9e345d2a82a631bc827c8617a/.gitlab/benchmarks/bp-runner.macrobenchmarks.fail-on-breach.yml>`_ for macrobenchmarks, and `bp-runner.microbenchmarks.fail-on-breach.yml <https://github.com/DataDog/dd-trace-py/blob/3cf3342a005c1ef9e345d2a82a631bc827c8617a/.gitlab/benchmarks/bp-runner.microbenchmarks.fail-on-breach.yml>`_ for microbenchmarks.

If bypassing is necessary, see `Performance quality gates - User Guide <https://datadoghq.atlassian.net/wiki/spaces/APMINT/pages/5158175217/Performance+quality+gates+-+User+Guide>`_ for more details.


Instructions
------------

Ensure you have followed the prerequisite steps above.

1. Draft a new GitHub release https://github.com/DataDog/dd-trace-py/releases/new

2. If release >=x.x.1 and release branch does not exist, create the release branch:

.. code-block:: bash

    $ git pull
    $ git checkout -b X.Y vX.Y.0
    $ git push -u origin X.Y

3. Set the target commit on the GitHub release draft. For release candidates, this should generally be the latest commit on main.
   For minor releases where the version ends with .0, the target commit must exactly match the commit of the latest relevant release candidate.
   For patch releases, the target should usually be the current head of the release branch.

4. Generate release notes from the relevant branch. For release candidates, this is usually main, and for patch releases it’s usually the x.y release branch. Copy and paste the latest section into the release’s description.

.. code-block:: bash

    $ git pull
    $ git checkout <branch>
    $ reno report --branch=origin/<branch> | pandoc -f rst -t gfm --wrap=none | less

5. Include an estimated end-of-life block at the top of the new release notes:

.. code-block::

    Estimated end-of-life date, accurate to within three months: MM-YYYY
    See [the support level definitions](https://docs.datadoghq.com/tracing/trace_collection/compatibility/python/#releases) for more information.

Where the EOL month is calculated thus: <this release line's start month> + <18 months>

6. Make sure the “Set as pre-release" box is CHECKED if publishing a release candidate.
   Make sure the “Set as latest release" box is CHECKED only if publishing a new minor release or a patch release for the latest minor version.
   Click “save draft”.

7. Share the link to the GitHub draft release with someone who can confirm it's correct

8. Click the the green “Publish release” button on the draft release. Double check that you have the correct check boxes checked and unchecked
   based on the release you’re about to publish. Wait for build and publish to succeed.
   The GitHub release will trigger the GitLab workflow that builds wheels and publishes to PyPI.

9. Check PyPI for the release.
