#!/usr/bin/env python3

import argparse
import csv
from pathlib import Path
import re
import subprocess
import sys
from typing import Optional


# Matches:
#   v4.5.0
#   v4.5.0rc4
#   4.5.0
#   4.5.0rc4
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?(rc(\d+))?$")


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


def parse_version(tag: str, tag_prefix: str) -> Optional[tuple]:
    if tag_prefix and tag.startswith(tag_prefix):
        version = tag[len(tag_prefix) :]
    else:
        version = tag

    # Match against the stripped version string (not the original tag)
    match = VERSION_RE.fullmatch(version)
    if not match:
        return None

    major, minor, patch, revision, rc_full, rc_num = match.groups()

    major = int(major)
    minor = int(minor)
    patch = int(patch)
    revision = int(revision) if revision else 0

    is_rc = rc_full is not None
    rc_number = int(rc_num) if rc_num else None

    return (
        tag,  # original tag
        version,  # raw version (no normalization)
        major,
        minor,
        patch,
        revision,
        is_rc,
        rc_number,
    )


def version_sort_key(parsed):
    _, _, major, minor, patch, revision, is_rc, rc_number = parsed

    # RCs come before stable
    release_rank = 0 if is_rc else 1
    rc_rank = rc_number if rc_number is not None else sys.maxsize

    return (major, minor, patch, revision, release_rank, rc_rank)


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

        parsed_versions = []
        skipped = []

        for tag in all_tags:
            parsed = parse_version(tag, args.tag_prefix)
            if parsed is None:
                skipped.append(tag)
                continue
            parsed_versions.append(parsed)

        parsed_versions.sort(key=version_sort_key)

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["tracer_version", "git_repository_url", "git_commit_sha"])

            for parsed in parsed_versions:
                tag, version, *_ = parsed

                commit = run_git(repo_path, "rev-list", "-n", "1", tag)
                if not commit:
                    print(f"WARNING: skipping {tag}", file=sys.stderr)
                    continue

                writer.writerow([version, repo_url, commit])

        print(f"Output written to: {output_path}")
        print(f"Total tags processed: {len(parsed_versions)}")
        print(f"Skipped tags: {len(skipped)}")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
