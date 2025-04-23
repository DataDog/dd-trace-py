"""
Script to format the integration registry YAML file to have blank lines between top-level integration list items.
"""

import pathlib
import sys
from typing import List
from typing import Optional


SCRIPT_DIR = pathlib.Path(__file__).parent.parent.resolve()
ROOT_DIR = SCRIPT_DIR.parent
REGISTRY_YAML_PATH = ROOT_DIR / "ddtrace" / "contrib" / "integration_registry" / "registry.yaml"


# This is the key that marks the start of a top-level integration list item
INTEGRATION_START_KEY = "- integration_name:"


def _read_file_lines(filepath: pathlib.Path) -> Optional[List[str]]:
    """Reads all lines from the file, handling potential errors."""
    try:
        with open(filepath, "r", encoding="utf-8") as infile:
            return infile.readlines()
    except IOError as e:
        print(f"  Error reading {filepath.relative_to(ROOT_DIR)}: {e}", file=sys.stderr)
        return None


def _process_lines_for_formatting(input_lines: List[str]) -> List[str]:
    """Adds blank lines between top-level integration list items."""
    output_lines = []
    found_first_integration = False
    for line in input_lines:
        stripped_line = line.lstrip()
        is_integration_start = stripped_line.startswith(INTEGRATION_START_KEY)

        if is_integration_start:
            if found_first_integration:
                if output_lines and output_lines[-1].strip() != "":
                    output_lines.append("\n")
            else:
                found_first_integration = True

        output_lines.append(line)
    return output_lines


def _write_file_lines(filepath: pathlib.Path, output_lines: List[str]) -> bool:
    """Writes lines back to a file, handling potential errors."""
    try:
        with open(filepath, "w", encoding="utf-8") as outfile:
            outfile.writelines(output_lines)
        return True
    except IOError as e:
        print(f"  Error writing formatted file {filepath.relative_to(ROOT_DIR)}: {e}", file=sys.stderr)
        return False


def main() -> int:
    """Main execution function."""
    print(f"Formatting YAML file: {REGISTRY_YAML_PATH.relative_to(ROOT_DIR)}")

    input_lines = _read_file_lines(REGISTRY_YAML_PATH)
    if input_lines is None:
        return 1

    output_lines = _process_lines_for_formatting(input_lines)

    if _write_file_lines(REGISTRY_YAML_PATH, output_lines):
        print(f"Successfully applied formatting to {REGISTRY_YAML_PATH.name}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
