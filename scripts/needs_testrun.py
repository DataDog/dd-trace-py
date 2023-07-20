#!/usr/bin/env python3

from argparse import ArgumentParser
import fnmatch
import json
from pathlib import Path
from subprocess import check_output
import sys
import typing as t


def get_changed_files() -> t.List[str]:
    return (
        check_output(
            [
                "gh",
                "pr",
                "list",
                "--search",
                check_output(["git", "rev-parse", "HEAD"]).decode("utf-8").strip(),
                "--json",
                "files",
                "--jq",
                ".[].files[].path",
            ]
        )
        .decode("utf-8")
        .strip()
        .splitlines()
    )


def get_patterns(suite: str) -> t.List[str]:
    def resolve(patterns: set, compos: dict) -> set:
        refs = {_ for _ in patterns if _.startswith("@")}
        resolved_patterns = patterns - refs

        # Recursively resolve references
        for ref in refs:
            try:
                resolved_patterns |= resolve(set(compos[ref[1:]]), compos)
            except KeyError:
                raise ValueError(f"Unknown component reference: {ref}")

        return resolved_patterns

    changemap_file = Path(__file__).parent.parent / "tests" / ".suitespec.json"
    with changemap_file.open() as f:
        cm = json.load(f)
        patterns = set(cm["suites"].get(suite, []))
        if not patterns:
            return patterns
        return resolve(patterns, cm["components"])


def main() -> bool:
    argp = ArgumentParser()
    argp.add_argument("suite", help="The suite to use", type=str)
    argp.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = argp.parse_args()

    patterns = get_patterns(args.suite)
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
