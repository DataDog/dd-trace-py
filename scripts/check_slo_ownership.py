#!/usr/bin/env python3
"""Lint SLO ownership for the microbenchmark performance gates.

The fail-on-breach gate (``check-slo-breaches`` CI job) scores benchmark
results against SLOs that ``scripts/gen_gitlab_config.py`` merges from one
per-team source file under ``.gitlab/benchmarks/slos/`` into the single
generated ``bp-runner.microbenchmarks.fail-on-breach.yml``. Each source file
is named ``<team-slug>.yml`` and lives on a path owned by that team in
``.github/CODEOWNERS``, so editing a threshold routes review to the team
automatically -- no inline owner comment is needed.

This script makes sure none of those SLOs ever gets orphaned:

  1. Every team file is owned by a team in CODEOWNERS (it does not silently
     fall through to the generic ``.gitlab/benchmarks`` default), and the
     owner matches the file name.
  2. Every SLO maps to a benchmark class + config that still exists (no SLO
     pointing at a deleted benchmark/config).
  3. No SLO is duplicated across team files (two teams must not own the same
     gate).
  4. Every benchmark config has an SLO entry in some team file, unless it is
     listed as an intentional exemption in
     ``.gitlab/benchmarks/slo-exemptions.yml`` (catches new benchmarks/configs
     added without a gate).
  5. Every benchmark's scenario class name follows the naming convention
     (CamelCase, no underscores) so it can be matched to SLO entries, and no
     two benchmark dirs share a lowercased class name.

Run via ``scripts/lint slo-ownership``. Exits non-zero if any check fails.
"""

from collections import defaultdict
from pathlib import Path
import re
import sys

from ruamel.yaml import YAML


ROOT = Path(__file__).parents[1]
BENCHMARKS = ROOT / "benchmarks"
SLOS_DIR = ROOT / ".gitlab" / "benchmarks" / "slos"
SLO_EXCEPTIONS = ROOT / ".gitlab" / "benchmarks" / "slo-exemptions.yml"

# codeowners.py lives in ddtrace/internal; load it the same way
# check_suitespec_coverage.py does.
sys.path.insert(0, str(ROOT / "ddtrace" / "internal"))
sys.path.insert(0, str(ROOT))
from codeowners import Codeowners  # noqa: E402


CODEOWNERS = Codeowners()

_YAML = YAML()


def _load_yaml(path: Path):
    if not path.exists():
        return None
    return _YAML.load(path.read_text())


SUITESPEC = _load_yaml(BENCHMARKS / "suitespec.yml") or {}
SUITES = SUITESPEC["suites"]

# Mirrors scripts/gen_gitlab_config.py so the names we compute match the ones
# the generator writes into the filtered SLO file.
BENCHMARK_CLASS_REGEX = r"class ([A-Za-z]+)\((bm\.)?Scenario(.+)?\)\:"
# A looser check used to tell "class name has an underscore" apart from "no
# Scenario subclass at all".
ANY_SCENARIO_CLASS_REGEX = re.compile(r"^class\s+(\w+)\s*\([^)]*\bScenario\b")
# Matches an SLO's ``- name:`` line. The config half is non-whitespace (all
# configs are single tokens).
SLO_LINE_REGEX = re.compile(r"^\s*- name: ([a-z0-9]+)-(\S+)\s*$")


def get_benchmark_class(suite_name: str) -> str | None:
    """Return the lowercased scenario class name for a benchmark dir, or None.

    None means the convention regex did not match: either there is no Scenario
    subclass at all, or the class name contains characters (like underscores)
    that break the ``<class>-<config>`` SLO naming scheme.
    """
    scenario = BENCHMARKS / suite_name / "scenario.py"
    if not scenario.exists():
        return None
    for line in scenario.read_text().splitlines():
        match = re.match(BENCHMARK_CLASS_REGEX, line)
        if match:
            return match.group(1).lower()
    return None


def has_scenario_subclass(suite_name: str) -> bool:
    scenario = BENCHMARKS / suite_name / "scenario.py"
    if not scenario.exists():
        return False
    return any(ANY_SCENARIO_CLASS_REGEX.match(line) for line in scenario.read_text().splitlines())


def get_configs(suite_name: str) -> list[str]:
    data = _load_yaml(BENCHMARKS / suite_name / "config.yaml")
    return list((data or {}).keys())


def parse_slos(path: Path) -> list[str]:
    """Return the scenario names declared in one team SLO file."""
    names: list[str] = []
    for line in path.read_text().splitlines():
        match = SLO_LINE_REGEX.match(line)
        if match:
            names.append(f"{match.group(1)}-{match.group(2)}")
    return names


def load_exemptions() -> tuple[set[str], set[str]]:
    data = _load_yaml(SLO_EXCEPTIONS) or {}
    ungated = set(data.get("ungated", []) or [])
    nonconformant = set(data.get("nonconformant_classnames", []) or [])
    return ungated, nonconformant


def main() -> int:
    errors: list[str] = []

    team_files = sorted(SLOS_DIR.glob("*.yml"))
    if not team_files:
        print(f"❌ no per-team SLO files found under {SLOS_DIR}")
        return 1

    ungated_exemptions, nonconformant_exemptions = load_exemptions()

    # Index class_lower -> suite dir. The SLO naming scheme derives the
    # scenario prefix from the lowercased class name, so two suites sharing a
    # class name would emit indistinguishable scenario names; track duplicates
    # separately so we can reject them instead of silently keeping the first.
    class_to_dir: dict[str, str] = {}
    duplicate_classes: dict[str, list[str]] = {}
    for suite_name in SUITES:
        cls = get_benchmark_class(suite_name)
        if cls is None:
            continue
        if cls in class_to_dir:
            duplicate_classes.setdefault(cls, [class_to_dir[cls]]).append(suite_name)
        else:
            class_to_dir[cls] = suite_name

    # Check 1: every team file is owned in CODEOWNERS by the team matching its
    # name, and not by the generic .gitlab/benchmarks default.
    all_slo_names: set[str] = set()
    seen_in_file: dict[str, list[str]] = defaultdict(list)
    for f in team_files:
        slug = f.stem  # e.g. apm-sdk-capabilities-python
        expected = f"@DataDog/{slug}"
        rel = str(f.relative_to(ROOT))
        owners = CODEOWNERS.of(rel)
        if not owners:
            errors.append(
                f"team file {rel} has no CODEOWNERS rule; it would fall through to the "
                f"generic .gitlab/benchmarks default"
            )
        elif expected not in owners:
            errors.append(
                f"team file {rel} is owned by {owners} but its name implies {expected}; "
                f"add a CODEOWNERS rule mapping it to {expected}"
            )
        for name in parse_slos(f):
            all_slo_names.add(name)
            seen_in_file[name].append(rel)

    # Check 3: no SLO is duplicated across team files.
    for name, files in sorted(seen_in_file.items()):
        if len(files) > 1:
            errors.append(f"SLO '{name}' appears in multiple team files: {files}")

    # Check 2: every SLO maps to a real benchmark class + config.
    for name in sorted(all_slo_names):
        cls_lower, _, config = name.partition("-")
        suite = class_to_dir.get(cls_lower)
        if suite is None:
            errors.append(f"SLO '{name}' references unknown benchmark class '{cls_lower}'")
        elif config not in get_configs(suite):
            errors.append(f"SLO '{name}' references unknown config in benchmarks/{suite}/config.yaml")

    # Check 4 + 5: every benchmark is conformant and every config is gated.
    for suite_name in SUITES:
        cls = get_benchmark_class(suite_name)
        if cls is None:
            has_subclass = has_scenario_subclass(suite_name)
            # A nonconformant exemption only covers the "class name has an
            # underscore" case. If the Scenario subclass was deleted entirely
            # that is different breakage and must still error, so only honor
            # the exemption while a subclass is actually present.
            if suite_name in nonconformant_exemptions and has_subclass:
                continue
            if has_subclass:
                errors.append(
                    f"benchmarks/{suite_name}/scenario.py class name does not follow the CamelCase "
                    f"convention (no underscores); it cannot be matched to SLO entries"
                )
            else:
                errors.append(f"benchmarks/{suite_name}/scenario.py has no Scenario subclass")
            continue
        for config in get_configs(suite_name):
            expected = f"{cls}-{config}"
            if expected in all_slo_names:
                continue
            if expected in ungated_exemptions:
                continue
            errors.append(
                f"benchmark '{suite_name}' config '{config}' has no SLO entry (expected '{expected}') "
                f"and is not in {SLO_EXCEPTIONS.name}"
            )

    # Check 5b: no two benchmark dirs share a lowercased class name.
    for cls, dirs in sorted(duplicate_classes.items()):
        errors.append(
            f"benchmark class prefix '{cls}' is shared by multiple suites {dirs}; "
            f"the SLO naming scheme requires unique scenario class names"
        )

    # Stale exemptions: an exemption that no longer corresponds to anything is
    # a maintenance hazard, so flag it too.
    for expected in sorted(ungated_exemptions):
        cls_lower, _, config = expected.partition("-")
        suite = class_to_dir.get(cls_lower)
        if suite is None or config not in get_configs(suite):
            errors.append(f"ungated exemption '{expected}' in {SLO_EXCEPTIONS.name} matches no benchmark config")
    for suite_name in sorted(nonconformant_exemptions):
        if suite_name not in SUITES:
            errors.append(f"nonconformant exemption '{suite_name}' in {SLO_EXCEPTIONS.name} matches no benchmark")
        elif get_benchmark_class(suite_name) is not None:
            errors.append(
                f"nonconformant exemption '{suite_name}' is no longer non-conformant; "
                f"remove it from {SLO_EXCEPTIONS.name}"
            )

    if errors:
        print(f"❌ {len(errors)} SLO ownership problem(s):")
        for e in errors:
            print(f"    {e}")
        return 1

    print(f"✨ 🍰 ✨ All {len(all_slo_names)} microbenchmark SLOs have owners and no orphans")
    return 0


if __name__ == "__main__":
    sys.exit(main())
