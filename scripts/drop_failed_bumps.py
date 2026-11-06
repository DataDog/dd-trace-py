#!/usr/bin/env scripts/uv-run-script
# -*- mode: python -*-
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "pyyaml>=6,<7",
#     "ruamel.yaml>=0.17.21",
# ]
# ///
"""Revert the bumped test locks of packages whose CI suites failed.

usage: <failed CI job names, one per line> | scripts/drop_failed_bumps.py [--base origin/main]

A job name like "dd-gitlab/contrib/fastapi 1/2" is the suite "contrib::fastapi". Every environment of a failed suite
whose lock changed relative to --base is a failed bump; its locks are restored to --base. Prints the dropped names.
Run scripts/integration_registry/update_and_format_registry.py afterwards to regenerate the registry files.
"""

import argparse
from pathlib import Path
import re
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tests.suitespec import LOCK_ROOT  # noqa: E402
from tests.suitespec import get_test_environments  # noqa: E402
from tests.suitespec import suite_job  # noqa: E402


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=PROJECT_ROOT, check=True, text=True, capture_output=True).stdout


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/main")
    base = parser.parse_args().base

    suites = get_test_environments(nightly=False)
    suite_by_job = {"/".join(suite_job(suite)): suite for suite in suites}
    failed_suites = set()
    for line in sys.stdin:
        job = re.sub(r"^dd-gitlab/| \d+/\d+$", "", line.strip())
        if job in suite_by_job:
            failed_suites.add(suite_by_job[job])
        elif line.startswith("dd-gitlab/") and job != "default-pipeline":
            print(
                f"::warning::failed check {line.strip()!r} matches no suite; its package is not dropped",
                file=sys.stderr,
            )

    changed = set(git("diff", "--name-only", base, "--", str(LOCK_ROOT)).split())
    environments = [environment for matrix in suites.values() for environment in matrix]
    bumped = {environment.name for environment in environments if str(environment.lockfile) in changed}
    dropped = {
        environment.name for suite in failed_suites for environment in suites[suite] if environment.name in bumped
    }

    for lockfile in sorted(str(e.lockfile) for e in environments if e.name in dropped):
        if git("ls-tree", base, "--", lockfile).strip():
            git("checkout", base, "--", lockfile)
        elif (PROJECT_ROOT / lockfile).exists():
            git("rm", "-f", "-q", lockfile)

    print("\n".join(sorted(dropped)))


if __name__ == "__main__":
    main()
