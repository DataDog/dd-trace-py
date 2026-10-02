#!/usr/bin/env scripts/uv-run-script
# /// script
# requires-python = ">=3.10"
# dependencies = ["ruamel.yaml>=0.17.21"]
# ///
"""Export suite dependency additions from coverage.py JSON reports."""

from __future__ import annotations

import argparse
import fnmatch
import json
from pathlib import Path
from pathlib import PurePosixPath
import sys

from ruamel.yaml import YAML


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tests.suitespec import _owners  # noqa: E402
from tests.suitespec import get_patterns  # noqa: E402
from tests.suitespec import get_suites  # noqa: E402


def source_path(filename: str, source_roots: tuple[str, ...]) -> str | None:
    """Map a captured source path to the checkout without guessing package locations."""
    path = PurePosixPath(filename.replace("\\", "/"))
    if ".." in path.parts:
        return None
    candidates = []
    for root in sorted(source_roots, key=len, reverse=True):
        try:
            candidates.append(path.relative_to(PurePosixPath(root.replace("\\", "/"))))
        except ValueError:
            continue
    candidates.append(path)
    for candidate in candidates:
        if candidate.parts[:1] == ("ddtrace",) and len(candidate.parts) > 1:
            return candidate.as_posix()
    return None


def observed_sources(reports: list[Path], source_roots: tuple[str, ...]) -> set[str]:
    """Union executed production files, excluding merely listed unexecuted sources."""
    observed: set[str] = set()
    for report in reports:
        data = json.loads(report.read_text())
        if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
            raise ValueError(f"{report}: expected coverage.py JSON with a files mapping")
        report_sources: set[str] = set()
        for filename, coverage in data["files"].items():
            if not isinstance(coverage, dict) or not isinstance(coverage.get("executed_lines"), list):
                raise ValueError(f"{report}: {filename}: expected an executed_lines list")
            lines = coverage["executed_lines"]
            if any(type(line) is not int or line < 1 for line in lines):
                raise ValueError(f"{report}: {filename}: invalid executed line number")
            path = source_path(filename, source_roots)
            if path is None and lines and "ddtrace" in PurePosixPath(filename.replace("\\", "/")).parts:
                raise ValueError(f"{report}: unmapped ddtrace source {filename}; supply --source-root")
            if path is not None and lines:
                report_sources.add(path)
        if not report_sources:
            raise ValueError(f"{report}: no executed ddtrace sources; check coverage collection and --source-root")
        observed.update(report_sources)
    return observed


def dependency_additions(suite: str, observed: set[str]) -> tuple[list[str], list[str]]:
    """Suggest component references or exact paths for observations missing from resolved triggers."""
    if suite not in get_suites():
        raise ValueError(f"Unknown suite: {suite}")
    patterns = get_patterns(suite)
    missing = sorted(path for path in observed if not any(fnmatch.fnmatchcase(path, p) for p in patterns))
    additions: set[str] = set()
    for path in missing:
        owners = _owners(path)
        if owners:
            additions.update(f"@{owner}" for owner in owners)
        else:
            additions.add(path)
    return sorted(additions), missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, help="Fully qualified suite name, e.g. debugging::debugger")
    parser.add_argument(
        "--coverage", required=True, action="append", type=Path, help="Coverage JSON; repeat to union runs"
    )
    parser.add_argument(
        "--source-root",
        action="append",
        default=[],
        help="Captured checkout or package parent path; repeat for multiple environments",
    )
    parser.add_argument("--check", action="store_true", help="Exit 1 when observed dependencies are missing")
    args = parser.parse_args(argv)
    try:
        if args.suite not in get_suites():
            raise ValueError(f"Unknown suite: {args.suite}")
        observed = observed_sources(args.coverage, (str(REPO), *args.source_root))
        additions, missing = dependency_additions(args.suite, observed)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    # Only additions are emitted: missing runtime observations never justify removing a trigger.
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.dump({"suites": {args.suite: {"paths": additions}}}, sys.stdout)
    print(f"{args.suite}: {len(observed)} observed sources, {len(missing)} missing triggers", file=sys.stderr)
    for path in missing:
        print(f"  {path}", file=sys.stderr)
    return int(args.check and bool(missing))


if __name__ == "__main__":
    sys.exit(main())
