from collections import defaultdict
import json
from pathlib import Path


INPUT_FILE = "flaky_tests_report.json"
OUTPUT_FILE = "restructured_flaky_tests.json"


def load_json(file_path):
    """Loads the JSON file."""
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def restructure_tests(data):
    """
    Restructures the JSON to be grouped by codeowner, then by category.
    {
        "@team-1": {
            "active": [...],
            "expiring_soon": [...],
            "expired": [...]
        },
        ...
    }
    """
    grouped_data = defaultdict(
        lambda: {  # Initializes default structure for each codeowner
            "active": [],
            "expiring_soon": [],
            "expired": [],
        }
    )

    for category, tests in data.items():
        for test in tests:
            codeowners = test.get("codeowners", ["Unknown"])
            for owner in codeowners:
                grouped_data[owner][category].append(test)

    return grouped_data


def save_json(data, file_path):
    """Saves the reformatted JSON to a file."""
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def main():
    """Main execution function."""
    if not Path(INPUT_FILE).exists():
        print(f"Error: {INPUT_FILE} not found.")
        return

    print(f"Loading JSON from {INPUT_FILE}...")
    test_data = load_json(INPUT_FILE)

    print("Restructuring test data...")
    transformed_data = restructure_tests(test_data)

    print(f"Saving structured JSON to {OUTPUT_FILE}...")
    save_json(transformed_data, OUTPUT_FILE)
    print("✅ Restructuring complete!")


if __name__ == "__main__":
    main()
