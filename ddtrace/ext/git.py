"""
tags for common git attributes
"""
import subprocess
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


def extract_git_info(author=True):
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
