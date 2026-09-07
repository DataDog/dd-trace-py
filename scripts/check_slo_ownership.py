#!/usr/bin/env python3
"""Validate microbenchmark SLO structural integrity (no orphans/duplicates).

SLO thresholds live in one per-team file under ``.gitlab/benchmarks/slos/``,
each owned by its team via ``.github/CODEOWNERS`` (so editing a threshold routes
review to that team). This script checks that none of those SLOs gets
orphaned:

  1. Every SLO maps to a benchmark class + config that still exists.
  2. No SLO is duplicated across team files.
  3. Every benchmark config has an SLO entry, unless it is a declared
     exemption in ``.gitlab/benchmarks/slo-exemptions.yml``.
  4. Scenario class names are unique and follow the CamelCase convention.

Ownership itself is enforced by CODEOWNERS via the file paths, so this script
does not check who owns what — only structural integrity.

Run via ``scripts/lint slo-ownership``. Exits non-zero if any check fails.
``scripts/gen_gitlab_config.py`` also calls ``validate()`` from here so the
``tests-gen`` CI job enforces the same checks on every PR.
"""

from pathlib import Path
import re
import sys

from ruamel.yaml import YAML


ROOT = Path(__file__).parents[1]
BENCHMARKS = ROOT / "benchmarks"
SLOS_DIR = ROOT / ".gitlab" / "benchmarks" / "slos"
EXEMPTIONS = ROOT / ".gitlab" / "benchmarks" / "slo-exemptions.yml"

_YAML = YAML()

# Mirrors scripts/gen_gitlab_config.py.
BENCHMARK_CLASS_REGEX = r"class ([A-Za-z]+)\((bm\.)?Scenario(.+)?\)\:"
BENCHMARK_SCENARIO_REGEX = re.compile(" +- name: ([a-z0-9]+)-.+")


def _get_benchmark_class_name(suite_name: str) -> str | None:
    """Return the lowercased scenario class name for a benchmark dir, or None."""
    scenario = BENCHMARKS / suite_name / "scenario.py"
    if not scenario.exists():
        return None
    for line in scenario.read_text().splitlines():
        match = re.match(BENCHMARK_CLASS_REGEX, line)
        if match:
            return match.group(1).lower()
    return None


def _configs(suite_name: str) -> set[str]:
    cfg = BENCHMARKS / suite_name / "config.yaml"
    if not cfg.exists():
        return set()
    return set((_YAML.load(cfg.read_text()) or {}).keys())


def validate() -> None:
    """Run all SLO structural checks; raise RuntimeError on any failure."""
    sys.path.insert(0, str(ROOT / "scripts"))
    sys.path.insert(0, str(ROOT / "tests"))
    import suitespec as _spec

    all_suites = _spec.get_suites()
    suites = {k: v for k, v in all_suites.items() if "benchmark" in v.get("type", "test")}

    # class_lower -> suite dir; track duplicates.
    class_to_dir: dict[str, str] = {}
    duplicate_classes: dict[str, list[str]] = {}
    for suite_name in suites:
        clean_name = suite_name.split("::")[-1]
        cls = _get_benchmark_class_name(clean_name)
        if cls is None:
            continue
        if cls in class_to_dir:
            duplicate_classes.setdefault(cls, [class_to_dir[cls]]).append(clean_name)
        else:
            class_to_dir[cls] = clean_name

    # Collect all SLO names across team files, tracking which file each is in.
    all_slos: set[str] = set()
    seen_in_file: dict[str, list[str]] = {}
    for src in sorted(SLOS_DIR.glob("*.yml")):
        rel = str(src.relative_to(ROOT))
        for line in src.read_text().splitlines():
            m = re.match(BENCHMARK_SCENARIO_REGEX, line)
            if m:
                name = line.split("- name:", 1)[1].strip()
                all_slos.add(name)
                seen_in_file.setdefault(name, []).append(rel)

    # Load exemptions.
    ungated_exemptions: set[str] = set()
    if EXEMPTIONS.exists():
        data = _YAML.load(EXEMPTIONS.read_text()) or {}
        ungated_exemptions = set(data.get("ungated", []) or [])

    errors: list[str] = []

    for name, files in sorted(seen_in_file.items()):
        if len(files) > 1:
            errors.append(f"SLO '{name}' appears in multiple team files: {files}")

    for name in sorted(all_slos):
        cls_lower, _, config = name.partition("-")
        suite = class_to_dir.get(cls_lower)
        if suite is None:
            errors.append(f"SLO '{name}' references unknown benchmark class '{cls_lower}'")
        elif config not in _configs(suite):
            errors.append(f"SLO '{name}' references unknown config in benchmarks/{suite}/config.yaml")

    for suite_name in suites:
        clean_name = suite_name.split("::")[-1]
        cls = _get_benchmark_class_name(clean_name)
        if cls is None:
            errors.append(f"benchmarks/{clean_name}/scenario.py has no conformant Scenario subclass")
            continue
        for config in _configs(clean_name):
            expected = f"{cls}-{config}"
            if expected in all_slos or expected in ungated_exemptions:
                continue
            errors.append(
                f"benchmark '{clean_name}' config '{config}' has no SLO entry (expected '{expected}') "
                f"and is not in slo-exemptions.yml"
            )

    for cls, dirs in sorted(duplicate_classes.items()):
        errors.append(
            f"benchmark class prefix '{cls}' is shared by multiple suites {dirs}; "
            f"the SLO naming scheme requires unique scenario class names"
        )

    for expected in sorted(ungated_exemptions):
        cls_lower, _, config = expected.partition("-")
        suite = class_to_dir.get(cls_lower)
        if suite is None or config not in _configs(suite):
            errors.append(f"ungated exemption '{expected}' in slo-exemptions.yml matches no benchmark config")

    if errors:
        raise RuntimeError(f"{len(errors)} SLO ownership problem(s):\n" + "\n".join(f"  {e}" for e in errors))


def main() -> int:
    try:
        validate()
    except RuntimeError as e:
        print(f"❌ {e}")
        return 1
    print("✨ 🍰 ✨ All microbenchmark SLOs are consistent (no orphans/duplicates)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
