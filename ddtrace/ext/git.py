"""
tags for common git attributes
"""
import logging
import subprocess
from typing import MutableMapping
from typing import Tuple

import six

from ddtrace.internal import compat


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

# Git Commit Commiter Email
COMMIT_COMMITTER_EMAIL = "git.commit.committer.email"

# Git Commit Committer Date (UTC)
COMMIT_COMMITTER_DATE = "git.commit.committer.date"

# Git Commit Message
COMMIT_MESSAGE = "git.commit.message"

log = logging.getLogger(__name__)


def extract_user_info(author=True):
    # type: (bool) -> Tuple[str, str, str]
    """Extract git commit author/committer info."""
    formatting = "--format=%an,%ae,%ad" if author else "--format=%cn,%ce,%cd"
    cmd = subprocess.Popen(
        ["git", "show", "-s", formatting, "--date=format:%Y-%m-%dT%H:%M:%S%z"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = cmd.communicate()
    if cmd.returncode == 0:
        name, email, date = compat.ensure_text(stdout).strip().split(",")
        return name, email, date
    raise ValueError(stderr)


def extract_repository_url():
    # type: () -> str
    cmd = subprocess.Popen(["git", "ls-remote", "--get-url"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = cmd.communicate()
    if cmd.returncode == 0:
        repository_url = compat.ensure_text(stdout).strip()
        return repository_url
    raise ValueError(stderr)


def extract_commit_message():
    # type: () -> str
    cmd = subprocess.Popen(["git", "show", "-s", "--format=%s"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = cmd.communicate()
    if cmd.returncode == 0:
        commit_message = compat.ensure_text(stdout).strip()
        return commit_message
    raise ValueError(stderr)


def extract_git_metadata():
    # type: () -> MutableMapping[str, str]
    """Extract git commit metadata."""
    tags = {}
    try:
        tags[REPOSITORY_URL] = extract_repository_url()
        tags[COMMIT_MESSAGE] = extract_commit_message()
        author = extract_user_info(author=True)
        tags[COMMIT_AUTHOR_NAME] = author[0]
        tags[COMMIT_AUTHOR_EMAIL] = author[1]
        tags[COMMIT_AUTHOR_DATE] = author[2]
        committer = extract_user_info(author=False)
        tags[COMMIT_COMMITTER_NAME] = committer[0]
        tags[COMMIT_COMMITTER_EMAIL] = committer[1]
        tags[COMMIT_COMMITTER_DATE] = committer[2]
    except GitNotFoundError:
        log.error("Git executable not found, cannot extract git metadata.")
    except ValueError:
        log.error("Error extracting git metadata, received non-zero return code: %s", exc_info=True)

    return tags
