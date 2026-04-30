#!/usr/bin/env python3
"""Manage supported-configurations.json and the generated Python module.

Reads the JSON registry and produces a Python module with:
- SUPPORTED_CONFIGURATIONS: frozenset of all registered env var names
- CONFIGURATION_ALIASES: dict mapping env var name to list of aliases
- DEPRECATED_CONFIGURATIONS: frozenset of deprecated env var names

Also verifies that every DD_*/OTEL_* var accessed in ddtrace/ is registered.

Usage:
    python scripts/supported_configurations.py                    # generate + verify registry
    python scripts/supported_configurations.py --check            # verify module is in sync + verify registry
    python scripts/supported_configurations.py --validate-remote  # validate against the central FPD registry
"""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


REPO_ROOT = Path(__file__).resolve().parent.parent
INPUT_FILE = REPO_ROOT / "supported-configurations.json"
OUTPUT_FILE = REPO_ROOT / "ddtrace" / "internal" / "settings" / "_supported_configurations.py"

# Cached libdatadog-build clone, kept outside the repo so no .gitignore entry
# is needed. Hosts the upstream config-inversion scripts that mirror the GitLab
# CI jobs:
#   .validate_supported_configurations_v2_local_file
#   .update_central_configurations_version_range_v2
LIBDATADOG_BUILD_DIR = Path(tempfile.gettempdir()) / "dd-trace-py-libdatadog-build"
LIBDATADOG_BUILD_REPO = "DataDog/libdatadog-build"

HEADER = """\
# AUTO-GENERATED from supported-configurations.json — do not edit manually.
# Run: python scripts/supported_configurations.py
#
# This module provides fast O(1) lookups for environment variable validation
# in ddtrace/internal/settings/env.py.
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


def check_registry(data: dict) -> int:
    """Verify every DD_*/OTEL_* var referenced in ddtrace/ is in the registry."""
    configs = data["supportedConfigurations"]
    all_known: set[str] = set(configs.keys()) | {
        alias for entries in configs.values() for entry in entries for alias in entry.get("aliases", [])
    }

    missing: set[str] = set()

    # Broad scan: any quoted string matching DD_*/OTEL_* in ddtrace/ Python files.
    # The generated registry module is skipped to avoid self-referential matches.
    pattern = re.compile(r'["\']((DD_|OTEL_)[A-Z][A-Z0-9_]*)["\']')
    exclude = {OUTPUT_FILE.resolve()}
    for path in (REPO_ROOT / "ddtrace").rglob("*.py"):
        if path.resolve() in exclude:
            continue
        for line in path.read_text(errors="ignore").splitlines():
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
            f"ERROR: {len(missing)} var(s) found in ddtrace/ but missing from {INPUT_FILE.name}.\n"
            f"Add them to the registry and re-run this script to regenerate the module:\n"
            f"\n  python scripts/supported_configurations.py\n"
            f"\nUnregistered vars:"
        )
        for var in sorted(missing):
            print(f"  {var}")
        return 1

    print(f"Registry is complete ({len(all_known)} entries, no unregistered vars).")
    return 0


def _clone_libdatadog_build() -> int:
    """Clone DataDog/libdatadog-build into LIBDATADOG_BUILD_DIR.

    Tries, in order: `gh repo clone` (uses whatever account is logged in via
    `gh auth login`), `git clone` over SSH, `git clone` over HTTPS. Each falls
    back to the next on failure so the script works in any environment that has
    at least one configured. Returns a process exit code (0 on success).
    """
    print(f"Cloning {LIBDATADOG_BUILD_REPO} into {LIBDATADOG_BUILD_DIR} ...")
    attempts: list[tuple[str, list[str]]] = []
    if shutil.which("gh") is not None:
        attempts.append(
            (
                "gh",
                ["gh", "repo", "clone", LIBDATADOG_BUILD_REPO, str(LIBDATADOG_BUILD_DIR), "--", "--depth=1", "--quiet"],
            )
        )
    if shutil.which("git") is not None:
        attempts.append(
            (
                "git+ssh",
                [
                    "git",
                    "clone",
                    "--depth=1",
                    "--quiet",
                    f"git@github.com:{LIBDATADOG_BUILD_REPO}.git",
                    str(LIBDATADOG_BUILD_DIR),
                ],
            )
        )
        attempts.append(
            (
                "git+https",
                [
                    "git",
                    "clone",
                    "--depth=1",
                    "--quiet",
                    f"https://github.com/{LIBDATADOG_BUILD_REPO}.git",
                    str(LIBDATADOG_BUILD_DIR),
                ],
            )
        )

    if not attempts:
        print("ERROR: neither 'gh' nor 'git' is installed; cannot clone the registry validation scripts.")
        return 1

    last_err = ""
    for label, cmd in attempts:
        try:
            shutil.rmtree(LIBDATADOG_BUILD_DIR, ignore_errors=True)
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return 0
        except subprocess.CalledProcessError as e:
            last_err = (e.stderr or e.stdout or "").strip()
            print(f"  {label} clone failed: {last_err.splitlines()[-1] if last_err else e.returncode}")

    print(
        "\nERROR: failed to clone DataDog/libdatadog-build (internal repo).\n"
        "Authenticate with one of:\n"
        "  - `gh auth login` (any Datadog-org account)\n"
        "  - SSH key registered with your GitHub account, or\n"
        "  - a credential helper for HTTPS (e.g. `gh auth setup-git`).\n"
        "Then re-run this command."
    )
    return 1


def validate_remote() -> int:
    """Validate supported-configurations.json against the central FPD registry.

    Mirrors the libdatadog-build CI job `.validate_supported_configurations_v2_local_file`.
    Vendors the upstream scripts into `.libdatadog-build/` (gitignored) on first run
    and runs them against `supported-configurations.json` using a venv at
    `.libdatadog-build/.venv`.
    """
    script = LIBDATADOG_BUILD_DIR / "scripts" / "config-inversion" / "config-inversion-local-validation.py"

    # Clone (or re-clone) if `.git` is missing or the expected script isn't where we expect
    # — covers aborted clones and upstream layout shifts.
    if not LIBDATADOG_BUILD_DIR.joinpath(".git").exists() or not script.exists():
        rc = _clone_libdatadog_build()
        if rc != 0:
            return rc
        if not script.exists():
            print(
                f"ERROR: validation script not found at {script} after a fresh clone; upstream layout may have changed."
            )
            return 1

    venv_dir = LIBDATADOG_BUILD_DIR / ".venv"
    venv_python = venv_dir / "bin" / "python"
    deps_marker = venv_dir / ".deps-installed"
    # Gate setup on a sentinel written *after* `pip install` succeeds, so a partially
    # built venv (e.g. ctrl-C between `python -m venv` and `pip install`) is rebuilt.
    if not venv_python.exists() or not deps_marker.exists():
        print(f"Creating venv at {venv_dir} ...")
        try:
            shutil.rmtree(venv_dir, ignore_errors=True)
            subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
            subprocess.run(
                [str(venv_dir / "bin" / "pip"), "install", "--quiet", "requests", "jsonschema", "pyyaml"],
                check=True,
            )
            deps_marker.touch()
        except subprocess.CalledProcessError as e:
            print(f"ERROR: failed to set up venv at {venv_dir}: {e}")
            return 1

    # Scrub parent-process Python env so the spawned venv doesn't accidentally
    # import packages from a surrounding hatch/venv shell. Pass only what the
    # downstream script needs.
    child_env = {k: v for k, v in os.environ.items() if k not in {"VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH"}}
    child_env["LOCAL_JSON_PATH"] = str(INPUT_FILE)

    return subprocess.run([str(venv_python), str(script)], env=child_env).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--check",
        action="store_true",
        help="Verify the generated module is in sync and the registry covers all DD_*/OTEL_* vars in ddtrace/.",
    )
    group.add_argument(
        "--validate-remote",
        action="store_true",
        help="Validate supported-configurations.json against the central Configuration Registry (FPD).",
    )
    args = parser.parse_args()

    if args.validate_remote:
        return validate_remote()

    check_mode = args.check

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
