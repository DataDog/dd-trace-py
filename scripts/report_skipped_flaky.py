from datetime import datetime
import json
import os
import re
import time

from codeowners import CodeOwners


FLAKY_PATTERN = re.compile(r'^\s*@flaky\(\s*(?:until=)?(\d+),?\s*(?:reason=\s*"(.*?)")?\s*\)?', re.IGNORECASE)
TEST_FUNCTION_PATTERN = re.compile(r"^\s*(?:async\s+)?def\s+(test_\w+)\s*\(", re.IGNORECASE)

TEST_DIR = "tests"
EXCLUDE_DIR = "tests/contrib/pytest"
CATEGORIES = {"active": [], "expiring_soon": [], "expired": []}

NOW = int(time.time())
ONE_WEEK_LATER = NOW + 7 * 24 * 60 * 60  # 1 week ahead


def load_codeowners():
    """Load the CODEOWNERS file and return a reusable CodeOwners object."""
    try:
        with open(".github/CODEOWNERS", encoding="utf-8") as f:
            return CodeOwners(f.read())
    except FileNotFoundError:
        print("Warning: CODEOWNERS file not found. Returning an empty mapping.")
        return CodeOwners("")


def categorize_test(timestamp):
    """Categorize test expiration based on timestamp."""
    if timestamp < NOW:
        return "expired"
    return "expiring_soon" if NOW <= timestamp <= ONE_WEEK_LATER else "active"


def extract_flaky_tests(file_path):
    flaky_tests = []
    try:
        with open(file_path, encoding="utf-8") as f:
            lines = f.readlines()

        test_name = None
        flaky_match = None

        for line in lines:
            if not flaky_match:
                flaky_match = FLAKY_PATTERN.match(line)

            func_match = TEST_FUNCTION_PATTERN.match(line)
            if flaky_match and func_match:
                test_name = func_match.group(1)

            if flaky_match and test_name:
                timestamp, reason = int(flaky_match.group(1)), flaky_match.group(2)
                flaky_tests.append((test_name, file_path, timestamp, reason))
                flaky_match = None  # Reset for the next test function
                test_name = None

    except (UnicodeDecodeError, IOError):
        print(f"Skipping file due to encoding issue: {file_path}")

    return flaky_tests


def get_flaky_tests():
    """Find and categorize all flaky tests in the repository."""
    test_data = {key: [] for key in CATEGORIES}
    codeowners = load_codeowners()
    counts = {}

    for root, _, files in os.walk(TEST_DIR):
        if EXCLUDE_DIR in root:
            continue

        for file in files:
            if not file.endswith(".py"):
                continue

            file_path = os.path.join(root, file)
            flaky_tests = extract_flaky_tests(file_path)
            if flaky_tests:
                counts[file_path] = len(flaky_tests)

            for test_name, path, timestamp, reason in flaky_tests:
                category = categorize_test(timestamp)
                test_data[category].append(
                    {
                        "test": test_name,
                        "file": path,
                        "expires": timestamp,
                        "reason": reason,
                        "codeowners": [owner[1] for owner in (codeowners.of(path) or [])] or ["Unknown"],
                    }
                )

    counts["total"] = sum(counts.values())
    return test_data, counts


def format_text_report(test_data):
    if not any(test_data.values()):
        return "✅ No flaky tests found!"

    output = ["*Flaky Test Report*\n"]
    emoji_map = {"expired": "🔴", "expiring_soon": "🟡", "active": "🟢"}
    count = 0

    for category, tests in test_data.items():
        output.append(f"  {emoji_map[category]} {category} {emoji_map[category]}")
        for test in tests:
            output.append(
                f"    `{test['test']}` in `{test['file']}`\n"
                f"      *Expires:* {datetime.fromtimestamp(test['expires']).strftime('%Y-%m-%d')}\n"
                f"      *Reason:* {test['reason'] or 'No reason provided'}\n"
                f"      *Codeowners:* {', '.join(test['codeowners'])}\n"
            )
            count += 1

    output.append(f"TOTAL: {count} flaky decorators")
    return "\n".join(output)


def main():
    # DEV: counts is for easy sanity checking against the grep command
    test_data, counts = get_flaky_tests()

    with open("flaky_tests_report.json", "w", encoding="utf-8") as f:
        json.dump(test_data, f, indent=4)

    print(format_text_report(test_data))


if __name__ == "__main__":
    main()
