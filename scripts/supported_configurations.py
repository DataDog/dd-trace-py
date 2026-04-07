#!/usr/bin/env python3
"""Manage supported-configurations.json and the generated Python module.

Reads the JSON registry and produces a Python module with:
- SUPPORTED_CONFIGURATIONS: frozenset of all registered env var names
- CONFIGURATION_ALIASES: dict mapping env var name to list of aliases
- DEPRECATED_CONFIGURATIONS: frozenset of deprecated env var names

Also verifies that every DD_*/OTEL_* var accessed in ddtrace/ is registered.

Usage:
    python scripts/supported_configurations.py           # generate + verify registry
    python scripts/supported_configurations.py --check   # verify module is in sync + verify registry
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import sys


REPO_ROOT = Path(__file__).resolve().parent.parent
INPUT_FILE = REPO_ROOT / "supported-configurations.json"
OUTPUT_FILE = REPO_ROOT / "ddtrace" / "internal" / "settings" / "_supported_configurations.py"

HEADER = """\
# AUTO-GENERATED from supported-configurations.json — do not edit manually.
# Run: python scripts/supported_configurations.py
#
# This module provides fast O(1) lookups for environment variable validation
# in ddtrace/internal/settings/env.py.

from __future__ import annotations

"""


def generate_module(data: dict) -> str:
    configs = data["supportedConfigurations"]
    all_names = sorted(configs.keys())

    # Schema v2: each value is a single-element array
    entries = {name: configs[name][0] for name in all_names}
    aliases = {name: e["aliases"] for name, e in entries.items() if e.get("aliases")}
    deprecated = sorted(name for name, e in entries.items() if e.get("deprecated"))

    supported = "\n".join(f'        "{n}",' for n in all_names)
    alias_lines = "\n".join(
        '    "{}": [{}],'.format(n, ", ".join('"{}"'.format(a) for a in v)) for n, v in aliases.items()
    )

    aliases_block = (
        f"CONFIGURATION_ALIASES: dict[str, list[str]] = {{\n{alias_lines}\n}}"
        if aliases
        else "CONFIGURATION_ALIASES: dict[str, list[str]] = {}"
    )
    deprecated_lines = "\n".join(f'        "{n}",' for n in deprecated)
    deprecated_block = (
        f"DEPRECATED_CONFIGURATIONS: frozenset[str] = frozenset(\n    {{\n{deprecated_lines}\n    }}\n)"
        if deprecated
        else "DEPRECATED_CONFIGURATIONS: frozenset[str] = frozenset()"
    )

    return f"""\
{HEADER}
SUPPORTED_CONFIGURATIONS: frozenset[str] = frozenset(
    {{
{supported}
    }}
)


{aliases_block}

{deprecated_block}
"""


_SCAN_SKIP_DIRS = frozenset(
    {
        ".git",
        ".tox",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        "build",
        "dist",
    }
)


def check_registry(data: dict) -> int:
    """Verify every DD_*/OTEL_* var referenced in the repo is in the registry.

    Any string literal matching DD_* or OTEL_* in Python files under the repo root
    is checked. Add a ``# sc-ignore`` comment on a line to suppress the check for
    that line (e.g. for build-time vars, intentionally-invalid test strings, or
    prefix constants that are not full var names).
    """
    configs = data["supportedConfigurations"]
    all_known: set[str] = set(configs.keys()) | {
        alias for entries in configs.values() for entry in entries for alias in entry.get("aliases", [])
    }

    missing: set[str] = set()

    # Broad scan: any quoted string matching DD_*/OTEL_* across all repo Python files.
    # Lines with '# sc-ignore' are excluded. The generated registry module is also skipped.
    pattern = re.compile(r'["\']((DD_|OTEL_)[A-Z][A-Z0-9_]*)["\']')
    exclude = {OUTPUT_FILE.resolve()}
    for path in REPO_ROOT.rglob("*.py"):
        if path.resolve() in exclude:
            continue
        if any(part in _SCAN_SKIP_DIRS for part in path.parts):
            continue
        for line in path.read_text(errors="ignore").splitlines():
            if "# sc-ignore" in line:
                continue
            for m in pattern.finditer(line):
                var = m.group(1)
                if not var.startswith("_DD_") and var not in all_known:
                    missing.add(var)

    # Dynamic vars from PATCH_MODULES: DD_TRACE_{NAME}_ENABLED, DD_{NAME}_SERVICE[_NAME]
    assigns = {
        node.targets[0].id: node.value
        for node in ast.walk(ast.parse((REPO_ROOT / "ddtrace" / "_monkey.py").read_text()))
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in ("PATCH_MODULES", "_NOT_PATCHABLE_VIA_ENVVAR")
    }
    patch_modules = [k.value for k in assigns["PATCH_MODULES"].keys if isinstance(k, ast.Constant)]
    not_patchable = {e.value for e in assigns["_NOT_PATCHABLE_VIA_ENVVAR"].elts if isinstance(e, ast.Constant)}

    for name in patch_modules:
        n = name.upper()
        if name not in not_patchable and f"DD_TRACE_{n}_ENABLED" not in all_known:
            missing.add(f"DD_TRACE_{n}_ENABLED")
        for var in (f"DD_{n}_SERVICE", f"DD_{n}_SERVICE_NAME"):
            if var not in all_known:
                missing.add(var)

    if missing:
        print(
            f"ERROR: {len(missing)} unregistered var(s) found in the repo. For each one, either:\n"
            f"  (a) Add it to {INPUT_FILE.name} and re-run this script to regenerate the module, or\n"
            f"  (b) Add '# sc-ignore' to the offending line if it is not a runtime config var\n"
            f"      (e.g. build-time vars, test fixtures, prefix constants, cross-tracer references).\n"
            f"\nUnregistered vars:"
        )
        for var in sorted(missing):
            print(f"  {var}")
        return 1

    print(f"Registry is complete ({len(all_known)} entries, no unregistered vars).")
    return 0


def main() -> int:
    check_mode = "--check" in sys.argv

    if not INPUT_FILE.exists():
        print(f"ERROR: {INPUT_FILE} not found.")
        return 1

    with open(INPUT_FILE) as f:
        data = json.load(f)

    content = generate_module(data)

    if check_mode:
        if not OUTPUT_FILE.exists():
            print(f"ERROR: {OUTPUT_FILE} does not exist. Run without --check to generate it.")
            return 1

        existing = OUTPUT_FILE.read_text()
        if existing != content:
            print(f"ERROR: {OUTPUT_FILE} is out of date. Run without --check to regenerate it.")
            return 1

        print("_supported_configurations.py is up to date.")
        return check_registry(data)

    # Generate mode: write the module then verify registry completeness.
    OUTPUT_FILE.write_text(content)
    config_count = len(data["supportedConfigurations"])
    print(f"Generated {OUTPUT_FILE} with {config_count} entries.")
    return check_registry(data)


if __name__ == "__main__":
    sys.exit(main())
