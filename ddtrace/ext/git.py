"""
tags for common git attributes
"""
import contextlib
import logging
import os
import random
import re
import subprocess
from typing import Dict
from typing import Generator
from typing import MutableMapping
from typing import Optional
from typing import Tuple
from typing import Union

import six

from ddtrace.internal import compat
from ddtrace.internal.compat import TemporaryDirectory
from ddtrace.internal.logger import get_logger


if six.PY2:
    GitNotFoundError = OSError
else:
    GitNotFoundError = FileNotFoundError

# Git Branch
BRANCH = "git.branch"

# Git Commit SHA
COMMIT_SHA = "git.commit.sha"

# Git Repository URL
REPOSITORY_URL = "git.repository_url"

# Git Tag
TAG = "git.tag"

# Git Commit Author Name
COMMIT_AUTHOR_NAME = "git.commit.author.name"

# Git Commit Author Email
COMMIT_AUTHOR_EMAIL = "git.commit.author.email"

# Git Commit Author Date (UTC)
COMMIT_AUTHOR_DATE = "git.commit.author.date"

# Git Commit Committer Name
COMMIT_COMMITTER_NAME = "git.commit.committer.name"

# Git Commit Committer Email
COMMIT_COMMITTER_EMAIL = "git.commit.committer.email"

# Git Commit Committer Date (UTC)
COMMIT_COMMITTER_DATE = "git.commit.committer.date"

# Git Commit Message
COMMIT_MESSAGE = "git.commit.message"

_RE_REFS = re.compile(r"^refs/(heads/)?")
_RE_ORIGIN = re.compile(r"^origin/")
_RE_TAGS = re.compile(r"^tags/")

log = get_logger(__name__)


def normalize_ref(name):
    # type: (Optional[str]) -> Optional[str]
    return _RE_TAGS.sub("", _RE_ORIGIN.sub("", _RE_REFS.sub("", name))) if name is not None else None


def is_ref_a_tag(ref):
    # type: (Optional[str]) -> bool
    return "tags/" in ref if ref else False


def _git_subprocess_cmd(cmd, cwd=None, std_in=None):
    # type: (Union[str, list[str]], Optional[str], Optional[bytes]) -> str
    """Helper for invoking the git CLI binary."""
    if isinstance(cmd, six.string_types):
        git_cmd = cmd.split(" ")
    else:
        git_cmd = cmd  # type: list[str]  # type: ignore[no-redef]
    git_cmd.insert(0, "git")
    process = subprocess.Popen(git_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE, cwd=cwd)
    stdout, stderr = process.communicate(input=std_in)
    if process.returncode == 0:
        return compat.ensure_text(stdout).strip()
    raise ValueError(compat.ensure_text(stderr).strip())


def _set_safe_directory():
    try:
        _git_subprocess_cmd("config --global --add safe.directory *")
    except GitNotFoundError:
        log.error("Git executable not found, cannot extract git metadata.")
    except ValueError:
        log.error("Error setting safe directory", exc_info=True)


def _extract_clone_defaultremotename(cwd=None):
    output = _git_subprocess_cmd("config --default origin --get clone.defaultRemoteName")
    return output


def _is_shallow_repository(cwd=None):
    # type: (Optional[str]) -> bool
    output = _git_subprocess_cmd("rev-parse --is-shallow-repository", cwd=cwd)
    return output.strip() == "true"


def _unshallow_repository(cwd=None):
    # type (Optional[str]) -> None
    remote_name = _extract_clone_defaultremotename(cwd)
    head_sha = extract_commit_sha(cwd)

    cmd = [
        "fetch",
        "--update-shallow",
        "--filter=blob:none",
        "--recurse-submodules=no",
        '--shallow-since="1 month ago"',
        remote_name,
        head_sha,
    ]

    _git_subprocess_cmd(cmd, cwd=cwd)


def extract_user_info(cwd=None):
    # type: (Optional[str]) -> Dict[str, Tuple[str, str, str]]
    """Extract commit author info from the git repository in the current directory or one specified by ``cwd``."""
    # Note: `git show -s --format... --date...` is supported since git 2.1.4 onwards
    stdout = _git_subprocess_cmd("show -s --format=%an,%ae,%ad,%cn,%ce,%cd --date=format:%Y-%m-%dT%H:%M:%S%z", cwd=cwd)
    author_name, author_email, author_date, committer_name, committer_email, committer_date = stdout.split(",")
    return {
        "author": (author_name, author_email, author_date),
        "committer": (committer_name, committer_email, committer_date),
    }


def extract_git_version(cwd=None):
    output = _git_subprocess_cmd("--version")
    version_info = tuple([int(part) for part in output.split()[-1].split(".")])
    return version_info


def extract_remote_url(cwd=None):
    remote_url = _git_subprocess_cmd("config --get remote.origin.url", cwd=cwd)
    return remote_url


def extract_latest_commits(cwd=None):
    latest_commits = _git_subprocess_cmd(["log", "--format=%H", "-n", "1000" '--since="1 month ago"'], cwd=cwd)
    return latest_commits.split("\n") if latest_commits else []


def get_rev_list_excluding_commits(commit_shas, cwd=None):
    return _get_rev_list(excluded_commit_shas=commit_shas, cwd=cwd)


def _get_rev_list(excluded_commit_shas=None, included_commit_shas=None, cwd=None):
    # type: (Optional[list[str]], Optional[list[str]], Optional[str]) -> str
    command = ["rev-list", "--objects", "--filter=blob:none"]
    if extract_git_version(cwd=cwd) >= (2, 23, 0):
        command.append('--since="1 month ago"')
        command.append("--no-object-names")
    command.append("HEAD")
    if excluded_commit_shas:
        exclusions = ["^%s" % sha for sha in excluded_commit_shas]
        command.extend(exclusions)
    if included_commit_shas:
        inclusions = ["%s" % sha for sha in included_commit_shas]
        command.extend(inclusions)
    commits = _git_subprocess_cmd(command, cwd=cwd)
    return commits


def extract_repository_url(cwd=None):
    # type: (Optional[str]) -> str
    """Extract the repository url from the git repository in the current directory or one specified by ``cwd``."""
    # Note: `git show ls-remote --get-url` is supported since git 2.6.7 onwards
    repository_url = _git_subprocess_cmd("ls-remote --get-url", cwd=cwd)
    return repository_url


def extract_commit_message(cwd=None):
    # type: (Optional[str]) -> str
    """Extract git commit message from the git repository in the current directory or one specified by ``cwd``."""
    # Note: `git show -s --format... --date...` is supported since git 2.1.4 onwards
    commit_message = _git_subprocess_cmd("show -s --format=%s", cwd=cwd)
    return commit_message


def extract_workspace_path(cwd=None):
    # type: (Optional[str]) -> str
    """Extract the root directory path from the git repository in the current directory or one specified by ``cwd``."""
    workspace_path = _git_subprocess_cmd("rev-parse --show-toplevel", cwd=cwd)
    return workspace_path


def extract_branch(cwd=None):
    # type: (Optional[str]) -> str
    """Extract git branch from the git repository in the current directory or one specified by ``cwd``."""
    branch = _git_subprocess_cmd("rev-parse --abbrev-ref HEAD", cwd=cwd)
    return branch


def extract_commit_sha(cwd=None):
    # type: (Optional[str]) -> str
    """Extract git commit SHA from the git repository in the current directory or one specified by ``cwd``."""
    commit_sha = _git_subprocess_cmd("rev-parse HEAD", cwd=cwd)
    return commit_sha


def extract_git_metadata(cwd=None):
    # type: (Optional[str]) -> Dict[str, Optional[str]]
    """Extract git commit metadata."""
    tags = {}  # type: Dict[str, Optional[str]]
    _set_safe_directory()
    try:
        tags[REPOSITORY_URL] = extract_repository_url(cwd=cwd)
        tags[COMMIT_MESSAGE] = extract_commit_message(cwd=cwd)
        users = extract_user_info(cwd=cwd)
        tags[COMMIT_AUTHOR_NAME] = users["author"][0]
        tags[COMMIT_AUTHOR_EMAIL] = users["author"][1]
        tags[COMMIT_AUTHOR_DATE] = users["author"][2]
        tags[COMMIT_COMMITTER_NAME] = users["committer"][0]
        tags[COMMIT_COMMITTER_EMAIL] = users["committer"][1]
        tags[COMMIT_COMMITTER_DATE] = users["committer"][2]
        tags[BRANCH] = extract_branch(cwd=cwd)
        tags[COMMIT_SHA] = extract_commit_sha(cwd=cwd)
    except GitNotFoundError:
        log.error("Git executable not found, cannot extract git metadata.")
    except ValueError as e:
        debug_mode = log.isEnabledFor(logging.DEBUG)
        stderr = str(e)
        log.error("Error extracting git metadata: %s", stderr, exc_info=debug_mode)

    return tags


def extract_user_git_metadata(env=None):
    # type: (Optional[MutableMapping[str, str]]) -> Dict[str, Optional[str]]
    """Extract git commit metadata from user-provided env vars."""
    env = os.environ if env is None else env

    branch = normalize_ref(env.get("DD_GIT_BRANCH"))
    tag = normalize_ref(env.get("DD_GIT_TAG"))

    # if DD_GIT_BRANCH is a tag, we associate its value to TAG instead of BRANCH
    if is_ref_a_tag(env.get("DD_GIT_BRANCH")):
        tag = branch
        branch = None

    tags = {}
    tags[REPOSITORY_URL] = env.get("DD_GIT_REPOSITORY_URL")
    tags[COMMIT_SHA] = env.get("DD_GIT_COMMIT_SHA")
    tags[BRANCH] = branch
    tags[TAG] = tag
    tags[COMMIT_MESSAGE] = env.get("DD_GIT_COMMIT_MESSAGE")
    tags[COMMIT_AUTHOR_DATE] = env.get("DD_GIT_COMMIT_AUTHOR_DATE")
    tags[COMMIT_AUTHOR_EMAIL] = env.get("DD_GIT_COMMIT_AUTHOR_EMAIL")
    tags[COMMIT_AUTHOR_NAME] = env.get("DD_GIT_COMMIT_AUTHOR_NAME")
    tags[COMMIT_COMMITTER_DATE] = env.get("DD_GIT_COMMIT_COMMITTER_DATE")
    tags[COMMIT_COMMITTER_EMAIL] = env.get("DD_GIT_COMMIT_COMMITTER_EMAIL")
    tags[COMMIT_COMMITTER_NAME] = env.get("DD_GIT_COMMIT_COMMITTER_NAME")

    return tags


@contextlib.contextmanager
def build_git_packfiles(revisions, cwd=None):
    # type: (str, Optional[str]) -> Generator
    basename = str(random.randint(1, 1000000))
    try:
        with TemporaryDirectory() as tempdir:
            prefix = "{tempdir}/{basename}".format(tempdir=tempdir, basename=basename)
            _git_subprocess_cmd(
                "pack-objects --compression=9 --max-pack-size=3m %s" % prefix, cwd=cwd, std_in=revisions.encode("utf-8")
            )
            yield prefix
    except ValueError:
        # The generation of pack files in the temporary folder (`TemporaryDirectory()`)
        # sometimes fails in certain CI setups with the error message
        # `unable to rename temporary pack file: Invalid cross-device link`.
        # The reason why is unclear.
        #
        # A workaround is to attempt to generate the pack files in `cwd`.
        # While this works most of the times, it's not ideal since it affects the git status.
        # This workaround is intended to be temporary.
        #
        # TODO: fix issue and remove workaround.
        if not cwd:
            cwd = os.getcwd()

        prefix = "{tempdir}/{basename}".format(tempdir=cwd, basename=basename)
        _git_subprocess_cmd(
            "pack-objects --compression=9 --max-pack-size=3m %s" % prefix, cwd=cwd, std_in=revisions.encode("utf-8")
        )
        yield prefix
