Release Process
===============

“Release candidates” are releases of dd-trace-py that are tagged with version strings including rc and marked in GitHub as “pre-releases”.
Most of the time spent “running the release” is actually spent creating and testing release candidates.
We create a release candidate, test it, and repeat until the test shows no errors or regressions.

Release candidates are based on the main branch.

The procedure described here can also be used to create patch releases (those that increment the patch version number in the major.minor.patch SemVer framework).

Prerequisites
-------------

1. Figure out the version of the library that you’re working on releasing.

2. Ensure the CI is green on the branch on which the release will be based.

Instructions
------------

Ensure you have followed the prerequisite steps above.

1. Draft a new Github release https://github.com/DataDog/dd-trace-py/releases/new

2. If release >=x.x.1 and release branch doesn’t exist, create the release branch:

    git pull
    git checkout -b X.Y vX.Y.0
    git push -u origin X.Y

3. Set the target commit on the GitHub release draft. For release candidates, this should generally be the latest commit on main.
   For minor releases where the version ends with .0, the target commit must exactly match the commit of the latest relevant release candidate.
   For patch releases, the target should usually be the current head of the release branch.

4. Generate release notes from the relevant branch. For release candidates, this is usually main, and for patch releases it’s usually the x.y release branch. Copypaste the latest section into the release’s description.

    git pull
    git checkout main
    reno report --branch=origin/main | pandoc -f rst -t gfm | less

5. Make sure the “Set as pre-release" box is CHECKED and the “Set as latest release" box is UNCHECKED. Click “save draft”.

6. Share the link to the GitHub draft release with someone who can confirm it's correct

7. Click the the green “Publish release” button on the draft release. Double check that you have the correct check boxes checked and unchecked
   based on the release you’re about to publish. Wait for build and publish to succeed.
   The GitHub release will trigger the GitLab workflow that builds wheels and publishes to PyPI.

9. Check PyPI for the release.
