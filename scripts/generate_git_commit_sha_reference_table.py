#!/usr/bin/env scripts/uv-run-script
# -*- mode: python -*-
# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "packaging>=23.1",
# ]
# ///
"""Generate a CSV mapping tracer versions to git commit SHAs."""

import argparse
import csv
from pathlib import Path
import subprocess
import sys
from typing import NamedTuple
from typing import Optional

from packaging.version import InvalidVersion
from packaging.version import Version


class ParsedTag(NamedTuple):
    tag: str
    version_str: str
    version: Version


def run_git(repo_path: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_path,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Git command failed: git {' '.join(args)}\n{e.stderr.strip()}") from e

    return result.stdout.strip()


def normalize_repo_url(url: str) -> str:
    url = url.strip()

    if url.endswith(".git"):
        url = url[:-4]

    if url.startswith("git@github.com:"):
        url = "https://github.com/" + url[len("git@github.com:") :]

    return url


def parse_version(tag: str, tag_prefix: str) -> Optional[ParsedTag]:
    if tag_prefix and tag.startswith(tag_prefix):
        version_str = tag[len(tag_prefix) :]
    else:
        version_str = tag

    try:
        version = Version(version_str)
    except InvalidVersion:
        return None

    return ParsedTag(tag, version_str, version)


def validate_repo(repo_path: Path):
    if not repo_path.exists():
        raise RuntimeError(f"Repository path does not exist: {repo_path}")
    if not (repo_path / ".git").exists():
        raise RuntimeError(f"Not a valid git repository: {repo_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate dd-trace-py reference table CSV")
    parser.add_argument("repo_path", help="Path to dd-trace-py repo")
    parser.add_argument(
        "-o",
        "--output",
        default="dd_trace_py_reference_table.csv",
        help="Output CSV file",
    )
    parser.add_argument(
        "--tag-prefix",
        default="v",
        help="Tag prefix (default: v)",
    )

    args = parser.parse_args()

    repo_path = Path(args.repo_path).resolve()
    output_path = Path(args.output).resolve()

    try:
        validate_repo(repo_path)

        repo_url = run_git(repo_path, "config", "--get", "remote.origin.url")
        repo_url = normalize_repo_url(repo_url)

        tags_output = run_git(repo_path, "tag", "-l", f"{args.tag_prefix}*")
        all_tags = [t.strip() for t in tags_output.splitlines() if t.strip()]

        parsed_versions: list[ParsedTag] = []
        skipped = []

        for tag in all_tags:
            parsed = parse_version(tag, args.tag_prefix)
            if parsed is None:
                skipped.append(tag)
                continue
            parsed_versions.append(parsed)

        parsed_versions.sort(key=lambda p: p.version)

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["tracer_version", "git_repository_url", "git_commit_sha"])

            for parsed in parsed_versions:
                commit = run_git(repo_path, "rev-list", "-n", "1", parsed.tag)
                if not commit:
                    print(f"WARNING: skipping {parsed.tag}", file=sys.stderr)
                    continue

                writer.writerow([str(parsed.version), repo_url, commit])

        print(f"Output written to: {output_path}")
        print(f"Total tags processed: {len(parsed_versions)}")
        print(f"Skipped tags: {len(skipped)}")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
