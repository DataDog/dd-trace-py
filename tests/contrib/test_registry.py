import csv
import pathlib
import pytest

# Determine the project root based on the test file location
# Assuming tests/contrib/test_registry.py, go up 3 levels
PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.resolve()
CONTRIB_INTERNAL_DIR = PROJECT_ROOT / "ddtrace" / "contrib" / "internal"
REGISTRY_CSV_PATH = PROJECT_ROOT / "ddtrace" / "contrib" / "integration_registry" / "registry.csv"
WHITELIST_CSV_PATH = PROJECT_ROOT / "ddtrace" / "contrib" / "integration_registry" / "whitelist.csv"

@pytest.fixture(scope="module")
def integration_names() -> set[str]:
    """Loads integration names from the registry CSV."""
    names = set()
    if not REGISTRY_CSV_PATH.exists():
        pytest.fail(f"Registry file not found: {REGISTRY_CSV_PATH}")
    try:
        with open(REGISTRY_CSV_PATH, "r", newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            # Ensure the expected column header exists
            if "Integration Name" not in reader.fieldnames:
                 pytest.fail(f"'Integration Name' column not found in {REGISTRY_CSV_PATH}")
            for row in reader:
                # Handle potential empty rows or missing data gracefully
                integration_name = row.get("Integration Name")
                if integration_name:
                    names.add(integration_name.strip())
    except Exception as e:
        pytest.fail(f"Error reading registry file {REGISTRY_CSV_PATH}: {e}")
    if not names:
        pytest.fail(f"No integration names found in {REGISTRY_CSV_PATH}")
    return names

@pytest.fixture(scope="module")
def whitelisted_dirs() -> set[str]:
    """Loads whitelisted directory names from the TXT file."""
    dirs = set()
    if not WHITELIST_CSV_PATH.exists():
        pytest.fail(f"Whitelist file not found: {WHITELIST_CSV_PATH}")
    try:
        with open(WHITELIST_CSV_PATH, "r", encoding="utf-8") as txtfile:
            for line in txtfile:
                stripped_line = line.strip()
                if stripped_line: # Avoid adding empty lines
                    dirs.add(stripped_line)
    except Exception as e:
        pytest.fail(f"Error reading whitelist file {WHITELIST_CSV_PATH}: {e}")
    # It's okay if this file is empty, so no check for emptiness here.
    return dirs

# --- Test Function ---

def test_all_internal_dirs_accounted_for(integration_names: set[str], whitelisted_dirs: set[str]):
    """
    Verify that every directory within ddtrace/contrib/internal is listed
    either in integration_registry.csv (as an 'Integration Name')
    or in non_integration_dirs.txt.
    """
    if not CONTRIB_INTERNAL_DIR.is_dir():
        pytest.fail(f"contrib/internal directory not found: {CONTRIB_INTERNAL_DIR}")

    accounted_for_dirs = integration_names.union(whitelisted_dirs)
    found_dirs = set()
    unaccounted_dirs = []

    for item in CONTRIB_INTERNAL_DIR.iterdir():
        if item.is_dir():
            dir_name = item.name
            found_dirs.add(dir_name)
            if dir_name not in accounted_for_dirs:
                unaccounted_dirs.append(dir_name)

    # Check for directories listed in files but not found on disk (optional sanity check)
    missing_registry_dirs = integration_names - found_dirs

    error_messages = []
    if unaccounted_dirs:
        error_messages.append(
            f"The following directories in {CONTRIB_INTERNAL_DIR} "
            f"were not found in either {REGISTRY_CSV_PATH.name} or {WHITELIST_CSV_PATH.name}:\n"
            f"{sorted(unaccounted_dirs)}"
        )
    if missing_registry_dirs:
         error_messages.append(
             f"The following directories listed in {REGISTRY_CSV_PATH.name} were not found in {CONTRIB_INTERNAL_DIR}:\n"
             f"{sorted(missing_registry_dirs)}"
         )
    # We might expect things in the whitelist not to exist (e.g., __pycache__) so only warn or skip this check
    # if missing_whitelist_dirs:
    #      error_messages.append(
    #          f"The following directories listed in {NON_INTEGRATION_TXT_PATH.name} were not found in {CONTRIB_INTERNAL_DIR}:\n"
    #          f"{sorted(missing_whitelist_dirs)}"
    #      )


    assert not error_messages, "\n\n".join(error_messages)
