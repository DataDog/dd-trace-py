#!/usr/bin/env python3
"""Lint SLO ownership for the microbenchmark performance gates.

The fail-on-breach gate (``check-slo-breaches`` CI job) scores benchmark
results against the SLOs declared in
``.gitlab/benchmarks/bp-runner.microbenchmarks.fail-on-breach.template.yml``.
Each SLO is one scenario config named ``<lowercased benchmark class>-<config>``
(see ``scripts/gen_gitlab_config.py``).

Ownership is recorded as a trailing comment on each SLO's ``- name:`` line::

    - name: span-start-finish  # owners: @DataDog/apm-sdk-capabilities-python

so that anyone editing a threshold sees the responsible team in the diff hunk
(the comment sits within the default 3-line context of the threshold line
below it) and knows whom to add as a reviewer.

This script makes sure none of those SLOs ever gets orphaned:

  1. Every SLO in the template has an ``# owners:`` comment (no SLO without an
     owner).
  2. Every SLO maps to a benchmark class + config that still exists (no SLO
     pointing at a deleted benchmark/config).
  3. Every benchmark config has an SLO entry, unless it is listed as an
     intentional exception in ``.gitlab/benchmarks/slo-exceptions.yml``
     (catches new benchmarks/configs added without a gate).
  4. Every benchmark's scenario class name follows the naming convention
     (CamelCase, no underscores) so it can be matched to SLO entries.

Run via ``scripts/lint slo-ownership``. Exits non-zero if any check fails.
"""

from pathlib import Path
import re
import sys

import yaml


ROOT = Path(__file__).parents[1]
BENCHMARKS = ROOT / "benchmarks"
SUITESPEC = yaml.safe_load((BENCHMARKS / "suitespec.yml").read_text())
SUITES = SUITESPEC["suites"]

SLO_TEMPLATE = ROOT / ".gitlab" / "benchmarks" / "bp-runner.microbenchmarks.fail-on-breach.template.yml"
SLO_EXCEPTIONS = ROOT / ".gitlab" / "benchmarks" / "slo-exceptions.yml"

# Mirrors scripts/gen_gitlab_config.py so the names we compute match the ones
# the generator writes into the filtered SLO file.
BENCHMARK_CLASS_REGEX = r"class ([A-Za-z]+)\((bm\.)?Scenario(.+)?\)\:"
# A looser check used to tell "class name has an underscore" apart from "no
# Scenario subclass at all".
ANY_SCENARIO_CLASS_REGEX = re.compile(r"^class\s+(\w+)\s*\([^)]*\bScenario\b")
# Matches an SLO's ``- name:`` line and splits the scenario name from an
# optional trailing ``# owners:`` comment. The config half is non-whitespace
# (all configs are single tokens), so the comment is separated cleanly.
SLO_LINE_REGEX = re.compile(r"^\s*- name: ([a-z0-9]+)-(\S+)(?:\s+#\s*owners:\s*(.+?))?\s*$")
# GitHub team mentions look like ``@org/team`` (note the slash), so allow
# word chars, hyphens, and slashes in the team slug.
OWNER_TOKEN_RE = re.compile(r"^@[\w/-]+$")


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
    cfg = BENCHMARKS / suite_name / "config.yaml"
    if not cfg.exists():
        return []
    return list((yaml.safe_load(cfg.read_text()) or {}).keys())


def parse_slos() -> list[tuple[str, str, str | None]]:
    """Return [(scenario_name, owners_raw_or_None)] from the template."""
    slos: list[tuple[str, str, str | None]] = []
    for line in SLO_TEMPLATE.read_text().splitlines():
        match = SLO_LINE_REGEX.match(line)
        if match:
            prefix, config, owners = match.group(1), match.group(2), match.group(3)
            slos.append((f"{prefix}-{config}", owners))
    return slos


def load_exceptions() -> tuple[set[str], set[str]]:
    data = yaml.safe_load(SLO_EXCEPTIONS.read_text()) if SLO_EXCEPTIONS.exists() else {}
    ungated = set(data.get("ungated", []) or [])
    nonconformant = set(data.get("nonconformant_classnames", []) or [])
    return ungated, nonconformant


def main() -> int:
    slos = parse_slos()
    if not slos:
        print("❌ no SLO scenarios found in template")
        return 1

    ungated_exceptions, nonconformant_exceptions = load_exceptions()

    errors: list[str] = []

    # Index class_lower -> suite dir, and the set of expected SLO names.
    class_to_dir: dict[str, str] = {}
    for suite_name in SUITES:
        cls = get_benchmark_class(suite_name)
        if cls is not None:
            class_to_dir.setdefault(cls, suite_name)

    slo_names = [name for name, _ in slos]

    # Check 1: every SLO has a well-formed, non-empty owner comment.
    for name, owners in slos:
        if owners is None:
            errors.append(f"SLO '{name}' has no '# owners:' comment in {SLO_TEMPLATE.name}")
            continue
        tokens = owners.split()
        if not tokens or not all(OWNER_TOKEN_RE.match(t) for t in tokens):
            errors.append(f"SLO '{name}' has malformed owners comment: {owners!r}")

    # Check 2: every SLO maps to a real benchmark class + config.
    for name, _ in slos:
        cls_lower, _, config = name.partition("-")
        suite = class_to_dir.get(cls_lower)
        if suite is None:
            errors.append(f"SLO '{name}' references unknown benchmark class '{cls_lower}'")
        elif config not in get_configs(suite):
            errors.append(f"SLO '{name}' references unknown config in benchmarks/{suite}/config.yaml")

    # Check 3 + 4: every benchmark is conformant and every config is gated.
    for suite_name in SUITES:
        cls = get_benchmark_class(suite_name)
        if cls is None:
            if suite_name in nonconformant_exceptions:
                continue
            if has_scenario_subclass(suite_name):
                errors.append(
                    f"benchmarks/{suite_name}/scenario.py class name does not follow the CamelCase "
                    f"convention (no underscores); it cannot be matched to SLO entries"
                )
            else:
                errors.append(f"benchmarks/{suite_name}/scenario.py has no Scenario subclass")
            continue
        for config in get_configs(suite_name):
            expected = f"{cls}-{config}"
            if expected in slo_names:
                continue
            if expected in ungated_exceptions:
                continue
            errors.append(
                f"benchmark '{suite_name}' config '{config}' has no SLO entry (expected '{expected}') "
                f"and is not in {SLO_EXCEPTIONS.name}"
            )

    # Stale exceptions: an exception that no longer corresponds to anything is
    # a maintenance hazard, so flag it too.
    for expected in sorted(ungated_exceptions):
        cls_lower, _, config = expected.partition("-")
        suite = class_to_dir.get(cls_lower)
        if suite is None or config not in get_configs(suite):
            errors.append(f"ungated exception '{expected}' in {SLO_EXCEPTIONS.name} matches no benchmark config")
    for suite_name in sorted(nonconformant_exceptions):
        if suite_name not in SUITES:
            errors.append(f"nonconformant exception '{suite_name}' in {SLO_EXCEPTIONS.name} matches no benchmark")
        elif get_benchmark_class(suite_name) is not None:
            errors.append(
                f"nonconformant exception '{suite_name}' is no longer non-conformant; "
                f"remove it from {SLO_EXCEPTIONS.name}"
            )

    if errors:
        print(f"❌ {len(errors)} SLO ownership problem(s):")
        for e in errors:
            print(f"    {e}")
        return 1

    print(f"✨ 🍰 ✨ All {len(slos)} microbenchmark SLOs have owners and no orphans")
    return 0


if __name__ == "__main__":
    sys.exit(main())
