"""
tags for common git attributes
"""
import subprocess
from typing import MutableMapping
from typing import Optional
from typing import Tuple

from ddtrace.internal import compat


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
    return "", "", ""


def extract_repository_url():
    # type: () -> str
    cmd = subprocess.Popen(["git", "ls-remote", "--get-url"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = cmd.communicate()
    if cmd.returncode == 0:
        repository_url = compat.ensure_text(stdout).strip()
        return repository_url
    return ""


def extract_commit_message():
    # type: () -> str
    cmd = subprocess.Popen(["git", "show", "-s", "--format=%s"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = cmd.communicate()
    if cmd.returncode == 0:
        commit_message = compat.ensure_text(stdout).strip()
        return commit_message
    return ""


def extract_git_metadata(tags):
    # type: (MutableMapping[str, Optional[str]]) -> MutableMapping[str, Optional[str]]
    """Extract git commit metadata. Tags already present take precedence."""
    try:
        extracted_repository_url = extract_repository_url()
    except (OSError, FileNotFoundError):
        extracted_repository_url = ""

    try:
        extracted_commit_message = extract_commit_message()
    except (OSError, FileNotFoundError):
        extracted_commit_message = ""

    try:
        author = extract_user_info(author=True)
    except (OSError, FileNotFoundError):
        author = "", "", ""
    author_name = tags.get(COMMIT_AUTHOR_NAME, author[0])
    author_email = tags.get(COMMIT_AUTHOR_EMAIL, author[1])

    try:
        committer = extract_user_info(author=False)
    except (OSError, FileNotFoundError):
        committer = "", "", ""
    committer_name = tags.get(COMMIT_COMMITTER_NAME, committer[0])
    committer_email = tags.get(COMMIT_COMMITTER_EMAIL, committer[1])

    return {
        REPOSITORY_URL: tags.get(REPOSITORY_URL, extracted_repository_url),
        COMMIT_MESSAGE: tags.get(COMMIT_MESSAGE, extracted_commit_message),
        COMMIT_AUTHOR_NAME: author_name,
        COMMIT_AUTHOR_EMAIL: author_email,
        COMMIT_AUTHOR_DATE: author[2],
        COMMIT_COMMITTER_NAME: committer_name,
        COMMIT_COMMITTER_EMAIL: committer_email,
        COMMIT_COMMITTER_DATE: committer[2],
    }
