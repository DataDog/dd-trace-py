#!/usr/bin/env python
import json
import os
import re
import subprocess
from typing import Optional  # noqa

import packaging.version


DEV_BRANCH_PATTERN = re.compile(r"^(.*/)?([0-9]+)\.x$")
RELEASE_BRANCH_PATTERN = re.compile(r"^(.*/)?([0-9]+\.[0-9]+)$")


def _mock_git_remote_tags(versions):
    # type: (list[str]) -> None
    """Helper used in tests to mock the output from ``git ls-remote --tags origin``"""
    import binascii
    import random
    from unittest import mock

    output = b"\n".join(
        [
            b"\t".join([binascii.hexlify(random.randbytes(20)), bytes(f"refs/tags/{version}", "utf-8")])
            for version in versions
        ]
    )

    mock.patch("subprocess.check_output", return_value=output).start()


def get_base_branch():
    # type: () -> str
    if "GITHUB_EVENT_PATH" not in os.environ:
        return os.environ.get("BASE_BRANCH", "origin/1.x")

    event = {}
    with open(os.environ["GITHUB_EVENT_PATH"], "r") as fp:
        event = json.load(fp)

    assert event, "Could not parse GitHub event from {}".format(os.environ["GITHUB_EVENT_PATH"])

    base_ref = event["pull_request"]["base"]["ref"]
    return base_ref


def is_dev_branch(branch):
    # type: (str) -> bool
    """Determine if the provided branch name matches our dev branch pattern.

    # Normal expected dev branch names
    >>> is_dev_branch("0.x")
    True
    >>> is_dev_branch("0.x")
    True
    >>> is_dev_branch("1.x")
    True
    >>> is_dev_branch("27.x")
    True
    >>> is_dev_branch("origin/1.x")
    True
    >>> is_dev_branch("refs/remotes/origin/1.x")
    True

    # User feature branches do not match
    >>> is_dev_branch("feature-branch")
    False
    >>> is_dev_branch("user/feature-branch")
    False

    # Release branches do not match
    >>> is_dev_branch("0.59")
    False
    >>> is_dev_branch("1.0")
    False
    >>> is_dev_branch("27.5")
    False
    >>> is_dev_branch("origin/1.0")
    False
    """
    return DEV_BRANCH_PATTERN.match(branch) is not None


def is_release_branch(branch):
    # type: (str) -> bool
    """Determine if the provided branch name matches our release branch pattern.

    # Release branches do not match
    >>> is_release_branch("0.59")
    True
    >>> is_release_branch("1.0")
    True
    >>> is_release_branch("27.5")
    True
    >>> is_release_branch("origin/1.0")
    True

    # Dev branches do not match
    >>> is_release_branch("0.x")
    False
    >>> is_release_branch("0.x")
    False
    >>> is_release_branch("1.x")
    False
    >>> is_release_branch("27.x")
    False
    >>> is_release_branch("origin/1.x")
    False

    # User feature branches do not match
    >>> is_release_branch("feature-branch")
    False
    >>> is_release_branch("user/feature-branch")
    False
    """
    return RELEASE_BRANCH_PATTERN.match(branch) is not None


def get_next_minor_version(branch):
    # type: (str) -> str
    """For a given dev branch get the next minor version

    # Random order to validate sorting is used
    >>> _mock_git_remote_tags([
    ...   "v0.50.5", "v0.50.4", "v0.50.6", "v0.51.0rc1", "v0.49.5", "v1.4.6",
    ...   "v1.20.5", "v0.34.0", "v6.0.0", "v5.0.0rc5"
    ... ])
    >>> get_next_minor_version("1.x")
    'v1.21.0'
    >>> get_next_minor_version("3.x")  # doesn't exist
    'v3.0.0'
    >>> get_next_minor_version("0.x")
    'v0.52.0'
    >>> get_next_minor_version("6.x")
    'v6.1.0'
    >>> get_next_minor_version("5.x")
    'v5.1.0'
    """
    match = DEV_BRANCH_PATTERN.match(branch)
    if not match:
        raise Exception("Cannot get next minor from branch {!r}".format(branch))

    release_line = int(match.group(2))
    release_versions = get_versions(major=release_line)
    if release_versions:
        most_recent = release_versions[0]
        return "v{}.{}.0".format(most_recent.major, most_recent.minor + 1)
    return "v{}.0.0".format(release_line)


def get_next_patch_version(branch):
    # type: (str) -> str
    """For a given release branch get the next patch version

    # Random order to validate sorting is used
    >>> _mock_git_remote_tags([
    ...   "v0.50.5", "v0.50.4", "v0.50.6", "v0.51.0rc1", "v0.49.5", "v1.4.6",
    ...   "v1.20.5", "v0.34.0", "v6.0.0", "v5.0.0rc5"
    ... ])
    >>> get_next_patch_version("1.4")
    'v1.4.7'
    >>> get_next_patch_version("3.0")  # doesn't exist
    'v3.0.0'
    >>> get_next_patch_version("0.51")  # don't increment if most recent is an rc
    'v0.51.0'
    >>> get_next_patch_version("6.0")
    'v6.0.1'
    >>> get_next_patch_version("1.20")
    'v1.20.6'
    """
    match = RELEASE_BRANCH_PATTERN.match(branch)
    if not match:
        raise Exception("Cannot get next patch from branch {!r}".format(branch))

    release = packaging.version.Version(match.group(2))
    release_versions = get_versions(major=release.major, minor=release.minor)
    if not release_versions:
        return "v{}.{}.0".format(release.major, release.minor)

    most_recent = release_versions[0]

    # If the most recent is a dev/prerelease then use that micro version, otherwise latest + 1
    if most_recent.is_devrelease or most_recent.is_prerelease:
        return "v{}.{}.{}".format(most_recent.major, most_recent.minor, most_recent.micro)
    return "v{}.{}.{}".format(most_recent.major, most_recent.minor, most_recent.micro + 1)


def get_versions(major, minor=None):
    # type: (int, Optional[int]) -> list[packaging.version.Version]
    """Get the currently released versions of this package from git remote tags

    >>> from packaging.version import Version
    >>> from unittest import mock
    >>> output = b'''
    ... 84e27ad91a10e57fd933e2e14c9216eab92d67c3	refs/tags/docs
    ... 84e27ad91a10e57fd933e2e14c9216eab92d67c4	refs/tags/non-release-tag
    ... 3956b6f2cd44dfd07133a8b28c91f1400b14aa7a	refs/tags/v0.49.0
    ... 0562918f51bde8707899759ee240669d6ff7be38	refs/tags/v0.49.0rc1
    ... 39f5079d5bfa34918d3d09c814c98df8832986c6	refs/tags/v0.49.1
    ... 9ee8ef7d2fef9fd735695b180caed42c317559cf	refs/tags/v0.50.0
    ... dba326054b6ecc7e6e6a0e0dfe7bf7d2caff3342	refs/tags/v0.50.0rc1
    ... 465edcd4e934e2f2090f7e3ff43cd3b644a43c01	refs/tags/v0.50.1
    ... 1a6bad3ec2cbfffab0f83ebf5b72fe786b76dbed	refs/tags/v0.59.0
    ... 7c18eca77ddda6b758d91c26c62714e0aabb9592	refs/tags/v0.59.0rc1
    ... 19d4c27089e32e6419e8261a7fa96779ea9699ed	refs/tags/v1.0.0rc1
    ... '''
    >>> mock.patch("subprocess.check_output", return_value=output).start()  # doctest: +ELLIPSIS
    <MagicMock name='check_output' id=...>

    # Only getting the major version
    >>> get_versions(major=1)
    [<Version('1.0.0rc1')>]
    >>> get_versions(major=0)
    [<Version('0.59.0')>, <Version('0.59.0rc1')>, <Version('0.50.1')>, <Version('0.50.0')>, <Version('0.50.0rc1')>, <Version('0.49.1')>, <Version('0.49.0')>, <Version('0.49.0rc1')>]
    >>> get_versions(major=45)
    []

    # Checking major and minor version
    >>> get_versions(major=0, minor=50)
    [<Version('0.50.1')>, <Version('0.50.0')>, <Version('0.50.0rc1')>]
    >>> get_versions(major=1, minor=0)
    [<Version('1.0.0rc1')>]
    >>> get_versions(major=1, minor=2)
    []

    # No results from git
    >>> mock.patch("subprocess.check_output", return_value=b"").start()  # doctest: +ELLIPSIS
    <MagicMock name='check_output' id=...>
    >>> get_versions(major=0)
    []
    """  # noqa: E501
    output = subprocess.check_output(["git", "ls-remote", "--tags", "origin"])

    versions = []
    for raw_line in output.splitlines():
        line = raw_line.decode().strip()
        if not line:
            continue

        _, _, tag = line.partition("refs/tags/")
        try:
            version = packaging.version.Version(tag)
        except Exception:
            continue

        # Filter
        if version.major != major:
            continue
        elif minor is not None and version.minor != minor:
            continue

        versions.append(version)

    # Sort most recent first
    return sorted(versions, reverse=True)


def main():
    # type: () -> None
    base_branch = get_base_branch()

    if is_dev_branch(base_branch):
        print("::set-output name=milestone::{}".format(get_next_minor_version(base_branch)))
    elif is_release_branch(base_branch):
        print("::set-output name=milestone::{}".format(get_next_patch_version(base_branch)))


if __name__ == "__main__":
    main()
