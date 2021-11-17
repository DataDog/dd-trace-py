"""
tags for common git attributes
"""
import os
import re
import subprocess
from typing import Dict
from typing import MutableMapping
from typing import Optional
from typing import Tuple

import six

from ddtrace.internal import compat
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


def _git_subprocess_cmd(cmd, cwd=None):
    # type: (str, Optional[str]) -> str
    """Helper for invoking the git CLI binary."""
    git_cmd = cmd.split(" ")
    git_cmd.insert(0, "git")
    process = subprocess.Popen(git_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
    stdout, stderr = process.communicate()
    if process.returncode == 0:
        return compat.ensure_text(stdout).strip()
    raise ValueError(stderr)


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
    except ValueError:
        log.error("Error extracting git metadata, received non-zero return code.", exc_info=True)

    return tags


def extract_user_git_metadata(env=None):
    # type: (Optional[MutableMapping[str, str]]) -> Dict[str, Optional[str]]
    """Extract git commit metadata from user-provided env vars."""
    env = os.environ if env is None else env

    branch = normalize_ref(env.get("DD_GIT_BRANCH"))
    tag = normalize_ref(env.get("DD_GIT_TAG"))

    if env.get("DD_GIT_TAG"):
        branch = None

    # if DD_GIT_BRANCH is a tag, we associate its value to TAG instead of BRANCH
    if "origin/tags" in env.get("DD_GIT_BRANCH", "") or "refs/heads/tags" in env.get("DD_GIT_BRANCH", ""):
        branch = None
        tag = normalize_ref(env.get("DD_GIT_BRANCH"))

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
