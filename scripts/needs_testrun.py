#!/usr/bin/env python3

from argparse import ArgumentParser
import fnmatch
import json
import os
from pathlib import Path
import re
from subprocess import check_output
import sys
import typing as t
from urllib.request import Request
from urllib.request import urlopen


BASE_BRANCH_PATTERN = re.compile(r':<span class="css-truncate-target">([^<]+)')
PR_NUMBER = os.environ.get("CIRCLE_PR_NUMBER")
SUITESPECFILE = Path(__file__).parents[1] / "tests" / ".suitespec.json"


def get_base_branch() -> str:
    if PR_NUMBER is None:
        raise ValueError("Cannot retrieve the PR number")

    pr_page_content = urlopen(f"https://github.com/DataDog/dd-trace-py/pull/{PR_NUMBER}").read().decode("utf-8")

    return BASE_BRANCH_PATTERN.search(pr_page_content).group(1)


def get_changed_files() -> t.List[str]:
    if PR_NUMBER is None:
        raise ValueError("Cannot retrieve the PR number")

    try:
        # Try with the GitHub REST API for the most accurate result
        url = f"https://api.github.com/repos/datadog/dd-trace-py/pulls/{PR_NUMBER}/files"
        headers = {"Accept": "application/vnd.github+json"}

        return [_["filename"] for _ in json.load(urlopen(Request(url, headers=headers)))]

    except Exception:
        # If that fails use the less accurate method of diffing against the base
        # branch
        print("WARNING: Failed to get changed files from GitHub API, using git diff instead")
        return (
            check_output(
                [
                    "git",
                    "diff",
                    "--name-only",
                    "HEAD",
                    get_base_branch(),
                ]
            )
            .decode("utf-8")
            .strip()
            .splitlines()
        )


def get_patterns(suite: str) -> t.List[str]:
    with SUITESPECFILE.open() as f:
        suitespec = json.load(f)

        compos = suitespec["components"]
        suite_patterns = set(suitespec["suites"].get(suite, []))

        def resolve(patterns: set) -> set:
            refs = {_ for _ in patterns if _.startswith("@")}
            resolved_patterns = patterns - refs

            # Recursively resolve references
            for ref in refs:
                try:
                    resolved_patterns |= resolve(set(compos[ref[1:]]))
                except KeyError:
                    raise ValueError(f"Unknown component reference: {ref}")

            return resolved_patterns

        return resolve(suite_patterns)


def main() -> bool:
    argp = ArgumentParser()
    argp.add_argument("suite", help="The suite to use", type=str)
    argp.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = argp.parse_args()

    try:
        patterns = get_patterns(args.suite)
    except Exception as e:
        print(f"Failed to get patterns: {e}. Running tests")
        return True
    if not patterns:
        # We don't have patterns so we run the tests
        if args.verbose:
            print(f"No patterns for suite '{args.suite}', running all tests")
        return True

    try:
        changed_files = get_changed_files()
    except Exception as e:
        print(f"Failed to get changed files: {e}. Running tests")
        return True
    if not changed_files:
        # No files changed, no need to run the tests
        if args.verbose:
            print("No files changed, not running tests")
        return False

    matches = [_ for p in patterns for _ in fnmatch.filter(changed_files, p)]

    if args.verbose:
        print("Changed files:", end="\n  ")
        print("\n  ".join(changed_files))
        print()
        print(f"Patterns for suite '{args.suite}':", end="\n  ")
        print("\n  ".join(patterns))
        print()
        if matches:
            print("Changed files matching patterns:", end="\n  ")
            print("\n  ".join(matches))
        else:
            print("No changed files match patterns")

    return bool(matches)


if __name__ == "__main__":
    sys.exit(not main())
