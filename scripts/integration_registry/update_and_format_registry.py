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

# --- Configuration ---
# Assuming this script is in ./scripts/
SCRIPT_DIR = pathlib.Path(__file__).parent.parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent.resolve()

# Paths to the scripts relative to PROJECT_ROOT
FRESHVENVS_SCRIPT = PROJECT_ROOT / "scripts" / "freshvenvs.py"
GENERATE_TABLE_SCRIPT = PROJECT_ROOT / "scripts" / "generate_table.py"
UPDATE_REGISTRY_SCRIPT = PROJECT_ROOT / "scripts" / "integration_registry" / "_update_integration_registry_versions.py"

# --- End Configuration ---

def run_script(script_path: pathlib.Path, *args: str) -> bool:
    """Runs a python script and checks for errors."""
    if not script_path.is_file():
        print(f"Error: Script not found: {script_path}", file=sys.stderr)
        return False

    command = [sys.executable, str(script_path)] + list(args)
    script_name = script_path.name
    print(f"\n--- Running {script_name} {' '.join(args)} ---")

    try:
        # Run from the project root directory
        result = subprocess.run(
            command,
            check=True,         # Raise exception on non-zero exit code
            capture_output=True,# Capture stdout/stderr
            text=True,          # Decode output as text
            cwd=PROJECT_ROOT
        )
        print(f"--- {script_name} Output ---")
        print(result.stdout)
        if result.stderr:
             print(f"--- {script_name} Stderr ---", file=sys.stderr)
             print(result.stderr, file=sys.stderr)
        print(f"--- {script_name} Succeeded ---")
        return True
    except FileNotFoundError:
         print(f"Error: Could not execute script. '{sys.executable}' or '{script_path}' not found?", file=sys.stderr)
         return False
    except subprocess.CalledProcessError as e:
         print(f"Error: {script_name} failed with exit code {e.returncode}", file=sys.stderr)
         print("--- Stdout ---", file=sys.stderr)
         print(e.stdout, file=sys.stderr)
         print("--- Stderr ---", file=sys.stderr)
         print(e.stderr, file=sys.stderr)
         return False
    except Exception as e:
         print(f"Error running {script_name}: {e}", file=sys.stderr)
         return False


def main():
    """Runs the version update workflow."""
    print(f"Starting version update workflow from: {PROJECT_ROOT}")

    # Step 1: Run freshvenvs.py generate
    if not run_script(FRESHVENVS_SCRIPT, "generate"):
        print("\nWorkflow failed at freshvenvs.py step.")
        sys.exit(1)

    # Step 2: Run generate_table.py
    # Check if generate_table.py exists, provide placeholder if not
    if GENERATE_TABLE_SCRIPT.is_file():
        if not run_script(GENERATE_TABLE_SCRIPT):
            print("\nWorkflow failed at generate_table.py step.")
            sys.exit(1)
    else:
        print(f"\nWarning: Script {GENERATE_TABLE_SCRIPT} not found. Skipping table generation.")
        print("Ensure supported_versions_table.csv is up-to-date manually if needed.")


    # Step 3: Run update_integration_registry_versions.py
    if not run_script(UPDATE_REGISTRY_SCRIPT):
        print("\nWorkflow failed at update_integration_registry_versions.py step.")
        sys.exit(1)

    print("\n--- Version Update Workflow Completed Successfully ---")


if __name__ == "__main__":
    main()
