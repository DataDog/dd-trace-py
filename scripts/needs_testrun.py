#!/usr/bin/env python3

from argparse import ArgumentParser
import fnmatch
from functools import cache
from itertools import count
import json
import logging
import os
from pathlib import Path
from subprocess import check_output
import sys
import typing as t
from urllib.parse import urlencode
from urllib.request import Request
from urllib.request import urlopen

from lxml import html


sys.path.insert(0, str(Path(__file__).parents[1]))

from tests.suitespec import get_patterns  # noqa


logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

LOGGER = logging.getLogger(__name__)


@cache
def get_base_branch(pr_number: int) -> str:
    """Get the base branch of a PR

    >>> import vcr
    >>> with vcr.use_cassette(
    ...   "scripts/vcr/needs_testrun.yaml",
    ...   filter_headers=["authorization", "user-agent"],
    ...   record_mode="none"):
    ...     get_base_branch(6412)
    ...     get_base_branch(11534)
    ...     get_base_branch(11690)
    '1.x'
    '2.15'
    'main'
    """

    pr_page_content = urlopen(f"https://github.com/DataDog/dd-trace-py/pull/{pr_number}").read().decode("utf-8")

    tree = html.fromstring(pr_page_content)
    base_ref = tree.find_class("base-ref")
    if base_ref:
        ref = base_ref[0].text_content().strip()
        # We might have `DataDog:1.x` or `DataDog:main` so we need to strip the prefix
        _, _, ref = ref.rpartition(":")
        return ref.strip()
    return "main"


@cache
def get_merge_base(pr_number: int) -> str:
    """Get the merge base of a PR."""
    return (
        check_output(
            [
                "git",
                "merge-base",
                "HEAD",
                get_base_branch(pr_number),
            ]
        )
        .decode("utf-8")
        .strip()
    )


@cache
def get_latest_commit_message() -> str:
    """Get the commit message of the last commit."""
    try:
        return check_output(["git", "log", "-1", "--pretty=%B"]).decode("utf-8").strip()
    except Exception:
        pass
    return ""


GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
if not GITHUB_TOKEN:
    try:
        GITHUB_TOKEN = (
            check_output(
                [
                    "aws",
                    "ssm",
                    "get-parameter",
                    "--region",
                    "us-east-1",
                    "--name",
                    f'ci.{os.environ["CI_PROJECT_NAME"]}.gh_token',
                    "--with-decryption",
                    "--query",
                    "Parameter.Value",
                    "--output=text",
                ]
            )
            .decode("utf-8")
            .strip()
        )
        LOGGER.info("GitHub token retrieved from SSM")
    except Exception:
        LOGGER.warning("No GitHub token available. Changes may not be detected accurately.", exc_info=True)
else:
    LOGGER.info("GitHub token retrieved from environment")


def github_api(path: str, query: t.Optional[dict] = None) -> t.Any:
    url = f"https://api.github.com/repos/datadog/dd-trace-py{path}"
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    if query is not None:
        url += "?" + urlencode(query)
    return json.load(urlopen(Request(url, headers=headers)))


@cache
def get_changed_files(pr_number: int, sha: t.Optional[str] = None) -> t.Set[str]:
    """Get the files changed in a PR

    Try with the GitHub REST API for the most accurate result. If that fails,
    or if there is a specific SHA given, use the less accurate method of
    diffing against a base commit, either the given SHA or the merge-base.

    >>> import vcr
    >>> with vcr.use_cassette(
    ...   "scripts/vcr/needs_testrun.yaml",
    ...   filter_headers=["authorization", "user-agent"],
    ...   record_mode="none"):
    ...     sorted(get_changed_files(6388))  # doctest: +NORMALIZE_WHITESPACE
    ['ddtrace/debugging/_expressions.py',
    'releasenotes/notes/fix-debugger-expressions-none-literal-30f3328d2e386f40.yaml',
    'tests/debugging/test_expressions.py']
    """
    if sha is None:
        files = set()
        try:
            for page in count(1):
                result = {_["filename"] for _ in github_api(f"/pulls/{pr_number}/files", {"page": page})}
                if not result:
                    return files
                files |= result
        except Exception:
            LOGGER.warning("Failed to get changed files from GitHub API", exc_info=True)

    diff_base = sha or get_merge_base(pr_number)
    LOGGER.info("Checking changed files against commit %s", diff_base)
    return set(check_output(["git", "diff", "--name-only", "HEAD", diff_base]).decode("utf-8").strip().splitlines())


@cache
def needs_testrun(suite: str, pr_number: int, sha: t.Optional[str] = None) -> bool:
    """Check if a testrun is needed for a suite and PR

    >>> import vcr
    >>> with vcr.use_cassette(
    ...   "scripts/vcr/needs_testrun.yaml",
    ...   filter_headers=["authorization", "user-agent"],
    ...   record_mode="none"):
    ...     needs_testrun("debugger", 6485)
    ...     needs_testrun("debugger", 6388)
    ...     needs_testrun("foobar", 6412)
    ...     needs_testrun("profile", 11690)
    True
    True
    True
    False
    """
    if "itr:noskip" in get_latest_commit_message().lower():
        return True
    try:
        patterns = get_patterns(suite)
    except Exception as exc:
        LOGGER.error("Failed to get patterns")
        LOGGER.error(exc)
        return True
    if not patterns:
        # We don't have patterns so we run the tests
        LOGGER.info("No patterns for suite '%s', running all tests", suite)
        return True

    try:
        changed_files = get_changed_files(pr_number, sha=sha)
    except Exception:
        LOGGER.error("Failed to get changed files")
        return True
    if not changed_files:
        # No files changed, no need to run the tests
        LOGGER.info("No files changed, not running tests")
        return False

    matches = [_ for p in patterns for _ in fnmatch.filter(changed_files, p)]

    LOGGER.info("Changed files:")
    for f in changed_files:
        LOGGER.info("  %s", f)
    LOGGER.info("Patterns for suite '%s':", suite)
    for p in patterns:
        LOGGER.info("  %s", p)
    if matches:
        LOGGER.info("Changed files matching patterns:")
        for m in matches:
            LOGGER.info("  %s", m)
    else:
        LOGGER.info("No changed files match patterns")

    return bool(matches)


@cache
def _get_pr_number() -> int:
    # CircleCI
    number = os.environ.get("CIRCLE_PR_NUMBER")
    if number is not None:
        return int(number)

    pr_url = os.environ.get("CIRCLE_PULL_REQUEST")
    if pr_url is not None:
        return int(pr_url.split("/")[-1])

    # GitLab
    ref_name = os.environ.get("CI_COMMIT_REF_NAME")
    if ref_name is not None:
        return int(github_api("/pulls", {"head": f"datadog:{ref_name}"})[0]["number"])

    raise RuntimeError("Could not determine PR number")


def for_each_testrun_needed(suites: t.List[str], action: t.Callable[[str], None], git_selections: t.Set[str]):
    try:
        pr_number = _get_pr_number()
    except Exception:
        pr_number = None

    for suite in suites:
        # If we don't have a valid PR number we run all tests
        # or "all" or current suite is annotated in git commit we run the suite
        # or the suite needs to be run based on the changed files
        if pr_number is None or (git_selections & {"all", suite}) or needs_testrun(suite, pr_number):
            action(suite)


def pr_matches_patterns(patterns: t.Set[str]) -> bool:
    try:
        changed_files = get_changed_files(_get_pr_number())
    except Exception:
        LOGGER.error("Failed to get changed files. Assuming the PR matches for precaution.")
        return True
    return bool([_ for p in patterns for _ in fnmatch.filter(changed_files, p)])


def extract_git_commit_selections(git_commit_message: str) -> t.Set[str]:
    """Extract the selected suites from git commit message."""
    suites = set()
    for token in git_commit_message.split():
        if token.lower().startswith("circleci:"):
            suites.update(token[len("circleci:") :].lower().split(","))
    return suites


def main() -> bool:
    argp = ArgumentParser()

    try:
        default_pr_number = _get_pr_number()
    except RuntimeError:
        default_pr_number = None

    argp.add_argument("suite", help="The suite to use", type=str)
    argp.add_argument("--pr", help="The PR number", type=int, default=default_pr_number)
    argp.add_argument(
        "--sha", help="Commit hash to use as diff base (defaults to PR merge root)", type=lambda v: v or None
    )
    argp.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = argp.parse_args()

    if args.verbose:
        LOGGER.setLevel(logging.INFO)

    if not args.pr:
        raise RuntimeError("Could not determine PR number")

    return needs_testrun(args.suite, args.pr, sha=args.sha)


if __name__ == "__main__":
    sys.exit(not main())
