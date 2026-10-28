#!/usr/bin/env python3
"""Detect suitespec suites that are duplicates, dead, or latently skipped.

Duplicate suites resolve to the same environments and job context, so they
run the same tests twice. Dead suites resolve to no environments. Skipped
suites are reported when they would otherwise run.

Usage:
    python scripts/find_duplicate_suites.py
    python scripts/find_duplicate_suites.py --paths
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
import sys


_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

import tests.suitespec as spec  # noqa: E402


def _suite_pattern(suite: str, config: dict) -> str:
    return config.get("pattern", suite)


def _job_context_key(config: dict) -> tuple:
    return (
        bool(config.get("snapshot", False)),
        tuple(config.get("services") or []),
        tuple(sorted((config.get("env") or {}).items())),
        bool(config.get("gpu", False)),
    )


def _environment_key(environment: spec.TestEnvironment) -> tuple:
    return (
        environment.name,
        environment.python,
        environment.direct_dependencies,
        environment.runs,
    )


def _print_suite(suite: str, config: dict, show_paths: bool) -> None:
    skip = " [skip]" if config.get("skip") else ""
    print(
        f"  - {suite}  (pattern={_suite_pattern(suite, config)!r}, "
        f"parallelism={config.get('parallelism')}, snapshot={config.get('snapshot', False)}){skip}"
    )
    if show_paths:
        for path in config.get("paths", []):
            print(f"      {path}")


def _report_duplicates(by_key: dict[tuple, list[str]], suites: dict[str, dict], show_paths: bool) -> bool:
    print("## Duplicate suites (same environments + snapshot/services/env)")
    found_active = False
    any_shown = False
    for key, group in sorted(by_key.items(), key=lambda item: (-len(item[1]), sorted(item[1]))):
        environments = key[0]
        if not environments or len(group) < 2:
            continue
        active = [suite for suite in group if not suites[suite].get("skip")]
        if len(active) >= 2:
            note = f"  ({len(active)} active — wasted CI)"
            found_active = True
        else:
            note = "  (only one active — skipped members would collide if un-skipped)"
        any_shown = True
        print(f"### {len(group)} suites share the same {len(environments)} environment(s){note}")
        for suite in sorted(group):
            _print_suite(suite, suites[suite], show_paths)
        print()
    if not any_shown:
        print("(none)\n")
    return found_active


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paths", action="store_true", help="also print each suite's trigger paths")
    args = parser.parse_args()

    suites = spec.get_suites()
    test_suites = {suite: config for suite, config in suites.items() if "benchmark" not in config.get("type", "test")}
    environments = spec.get_test_environments(nightly=False)
    matched = {
        suite: frozenset(_environment_key(environment) for environment in environments.get(suite, ()))
        for suite in test_suites
    }

    skipped = sum(1 for config in test_suites.values() if config.get("skip"))
    benchmarks = len(suites) - len(test_suites)
    print(f"# {len(test_suites)} test suites ({benchmarks} benchmark suites skipped), {skipped} skipped")
    print()

    by_key: dict[tuple, list[str]] = defaultdict(list)
    for suite, environment_set in matched.items():
        by_key[(environment_set, _job_context_key(test_suites[suite]))].append(suite)
    has_duplicates = _report_duplicates(by_key, test_suites, args.paths)

    dead = sorted(suite for suite, environment_set in matched.items() if not environment_set)
    print("## Dead suites (resolve to no environments — would emit zero jobs)")
    if dead:
        for suite in dead:
            _print_suite(suite, test_suites[suite], args.paths)
        print()
    else:
        print("(none)\n")

    latent = sorted(
        suite for suite, environment_set in matched.items() if test_suites[suite].get("skip") and environment_set
    )
    print("## Skipped suites (latent: would run if un-skipped)")
    if latent:
        for suite in latent:
            config = test_suites[suite]
            print(
                f"  - {suite}  (pattern={_suite_pattern(suite, config)!r}, "
                f"matches {len(matched[suite])} environment(s))"
            )
            if args.paths:
                for path in config.get("paths", []):
                    print(f"      {path}")
        print()
    else:
        print("(none)\n")

    has_active_dead = any(not test_suites[suite].get("skip") for suite in dead)
    return 1 if has_duplicates or has_active_dead else 0


if __name__ == "__main__":
    raise SystemExit(main())
