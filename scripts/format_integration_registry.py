import pathlib
import sys

# --- Configuration ---
SCRIPT_DIR = pathlib.Path(__file__).parent.resolve()
ROOT_DIR = SCRIPT_DIR.parent
REGISTRY_YAML_PATH = ROOT_DIR / "ddtrace" / "contrib" / "integration_registry" / "registry.yaml"

# Define the expected pattern for the start of a top-level integration list item
# It should start with indentation, a dash, a space, and then the key "integration_name:"
INTEGRATION_START_KEY = "- integration_name:"
# --- End Configuration ---

def format_yaml_with_newlines(yaml_path: pathlib.Path):
    """Reads a YAML file and rewrites it with blank lines between top-level list items."""
    print(f"Formatting YAML file: {yaml_path}")
    if not yaml_path.is_file():
        print(f"  Error: Cannot format - file not found: {yaml_path}", file=sys.stderr)
        return False # Indicate failure

    try:
        with open(yaml_path, 'r', encoding='utf-8') as infile:
            input_lines = infile.readlines()
    except IOError as e:
        print(f"  Error reading {yaml_path} for formatting: {e}", file=sys.stderr)
        return False # Indicate failure

    output_lines = []
    found_first_integration = False

    for line in input_lines:
        # Check if the line marks the beginning of a top-level integration item
        # by checking if the stripped line starts with '- integration_name:'
        stripped_line = line.lstrip() # Remove leading whitespace only
        is_integration_start = stripped_line.startswith(INTEGRATION_START_KEY)

        if is_integration_start:
            if found_first_integration:
                # Add blank line only if previous line wasn't already blank
                if output_lines and output_lines[-1].strip() != "":
                    output_lines.append("\n")
            else:
                found_first_integration = True

        output_lines.append(line) # Preserve original line ending

    try:
        with open(yaml_path, 'w', encoding='utf-8') as outfile:
            outfile.writelines(output_lines)
        print(f"Successfully applied formatting to {yaml_path.name}")
        return True # Indicate success
    except IOError as e:
        print(f"  Error writing formatted file {yaml_path}: {e}", file=sys.stderr)
        return False # Indicate failure

if __name__ == "__main__":
    if format_yaml_with_newlines(REGISTRY_YAML_PATH):
        sys.exit(0) # Exit success
    else:
        sys.exit(1) # Exit failure