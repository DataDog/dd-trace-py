#!/usr/bin/env python3
"""
Runs the sequence of scripts required to update supported versions
and the integration registry.

1. Regenerates the supported versions JSON data (freshvenvs.py).
2. Generates the supported versions CSV table (generate_table.py).
3. Updates the integration registry YAML with the new versions (update_integration_registry_versions.py).
"""

import pathlib
import subprocess
import sys
from typing import List


SCRIPT_DIR = pathlib.Path(__file__).parent.parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent.resolve()
PATH_FRESHVENVS_SCRIPT = PROJECT_ROOT / "scripts" / "freshvenvs.py"
PATH_GENERATE_TABLE_SCRIPT = PROJECT_ROOT / "scripts" / "generate_table.py"
PATH_UPDATE_REGISTRY_SCRIPT = (
    PROJECT_ROOT / "scripts" / "integration_registry" / "_update_integration_registry_versions.py"
)
PATH_FORMAT_REGISTRY_SCRIPT = PROJECT_ROOT / "scripts" / "integration_registry" / "_format_integration_registry.py"


def _run_script(script_path: pathlib.Path, *args: str) -> bool:
    """Executes a given Python script using the current interpreter and handles output/errors."""
    command: List[str] = [sys.executable, str(script_path)] + list(args)
    script_name: str = script_path.name
    print(f" -> Running {script_path.relative_to(PROJECT_ROOT)} {' '.join(args)}...")

    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=60)
        if result.stdout and result.stdout.strip():
            indented_stdout = "\n".join([f"    {line}" for line in result.stdout.strip().splitlines()])
            print(indented_stdout)

        if result.stderr and result.stderr.strip():
            print(f"--- {script_name} Stderr ---", file=sys.stderr)
            print(result.stderr.strip(), file=sys.stderr)

        return True
    except subprocess.TimeoutExpired:
        print(f"Error: {script_path.relative_to(PROJECT_ROOT)} timed out after 60 seconds.", file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(f"Error: {script_path.relative_to(PROJECT_ROOT)} failed with exit code {e.returncode}", file=sys.stderr)
        if e.stdout and e.stdout.strip():
            print("--- Stdout ---", e.stdout.strip(), file=sys.stderr)
        if e.stderr and e.stderr.strip():
            print("--- Stderr ---", e.stderr.strip(), file=sys.stderr)
    except Exception as e:
        print(f"Error running {script_path.relative_to(PROJECT_ROOT)}: {e}", file=sys.stderr)

    print(f" -> {script_path.relative_to(PROJECT_ROOT)} FAILED.")
    return False


def main() -> int:
    """Runs the version update workflow sequentially."""
    print(f"\nStarting Version Update Workflow (from: {PROJECT_ROOT.name})")
    print("=" * 60)

    # Step 1: Regenerate intermediate version data
    if not _run_script(PATH_FRESHVENVS_SCRIPT, "generate"):
        print(f"\nWorkflow aborted: {PATH_FRESHVENVS_SCRIPT.relative_to(PROJECT_ROOT)} failed.")
        print("=" * 60)
        return 1

    # Step 2: Generate the supported versions table CSV
    if not _run_script(PATH_GENERATE_TABLE_SCRIPT):
        print(f"\nWorkflow aborted: {PATH_GENERATE_TABLE_SCRIPT.relative_to(PROJECT_ROOT)} failed.")
        print("=" * 60)
        return 1

    # Step 3: Update the registry YAML using the generated table of tested dependency versions
    if not _run_script(PATH_UPDATE_REGISTRY_SCRIPT):
        print(f"\nWorkflow aborted: {PATH_UPDATE_REGISTRY_SCRIPT.relative_to(PROJECT_ROOT)} failed.")
        print("=" * 60)
        return 1

    # Step 4: Format the registry YAML
    if not _run_script(PATH_FORMAT_REGISTRY_SCRIPT):
        print(f"\nWorkflow aborted: {PATH_FORMAT_REGISTRY_SCRIPT.relative_to(PROJECT_ROOT)} failed.")
        print("=" * 60)
        return 1

    print("=" * 60)
    print("--- Version Update Workflow Completed Successfully ---")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
