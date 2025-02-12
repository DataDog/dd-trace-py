from datetime import datetime
import os
import re
import subprocess
import time


FLAKY_PATTERN = re.compile(r'^\s*@flaky\(\s*(?:until=)?(\d+),?\s*(?:reason=\s*"(.*?)")?\s*\)?', re.IGNORECASE)
TEST_FUNCTION_PATTERN = re.compile(r"^\s*def\s+(test_\w+)\s*\(", re.IGNORECASE)  # Captures test function names

TEST_DIR = "tests"
EXCLUDE_DIR = "tests/contrib/pytest"

ACTIVE = "active"
SOON = "expiring_soon"
EXPIRED = "expired"
CATEGORIES = [ACTIVE, SOON, EXPIRED]

NOW = int(time.time())
ONE_WEEK_LATER = NOW + 7 * 24 * 60 * 60  # 1 week from now


def get_flaky_tests():
    """Finds all tests with @flaky decorators and extracts their expiration timestamps."""
    test_data = {ACTIVE: [], SOON: [], EXPIRED: []}
    counts = {}
    total = 0
    for root, _, files in os.walk(TEST_DIR):
        if EXCLUDE_DIR in root:
            continue

        for file in files:
            if not file.endswith(".py"):
                continue

            file_path = os.path.join(root, file)
            counts[file_path] = 0
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()

                test_name = None
                flaky_match = None
                for line in lines:
                    if not flaky_match:
                        flaky_match = FLAKY_PATTERN.match(line)

                    func_match = TEST_FUNCTION_PATTERN.match(line)
                    if func_match:
                        test_name = func_match.group(1)
                    if flaky_match and test_name:
                        timestamp = int(flaky_match.group(1))
                        reason = flaky_match.group(2)
                        category = categorize_test(timestamp)
                        codeowners = get_codeowners(file_path)

                        test_data[category].append(
                            {
                                "test": test_name,
                                "file": file_path,
                                "expires": timestamp,
                                "reason": reason,
                                "codeowners": codeowners,
                            }
                        )
                        total += 1
                        counts[file_path] += 1
                        flaky_match = None

            except (UnicodeDecodeError, IOError):
                print(f"Skipping file due to encoding issue: {file_path}")
            if counts[file_path] == 0:
                del counts[file_path]
    counts["total"] = total
    return test_data, counts


def categorize_test(timestamp):
    """Categorize test expiration based on timestamp."""
    if timestamp < NOW:
        return EXPIRED
    elif NOW <= timestamp <= ONE_WEEK_LATER:
        return SOON
    return ACTIVE


def get_codeowners(file_path):
    """Fetch CODEOWNER info and extract only the owners."""
    try:
        result = subprocess.run(["codeowners", file_path], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip().split()[1:]  # Skip the filename (first entry)
        return []
    except FileNotFoundError:
        return []


def format_text_report(test_data):
    """Format the output to match Slack's text format."""
    if not test_data:
        return "✅ No flaky tests found!"

    output = ["*Flaky Test Report*\n"]
    count = 0
    for category in CATEGORIES:
        tests = test_data[category]
        emoji = "🔴" if category == "expired" else "🟡" if category == "expiring_soon" else "🟢"
        output.append(f"  {emoji} {category} {emoji}")
        for test in tests:
            output.append(
                f"    `{test['test']}` in `{test['file']}`\n"
                f"      *Expires:* {datetime.fromtimestamp(test['expires']).strftime('%Y-%m-%d')}\n"
                f"      *Reason:* {test['reason']}\n"
                f"      *Codeowners:* {test['codeowners']}\n"
            )
            count += 1
    output.append(f"TOTAL: {count} flaky decorators")
    return "\n".join(output)


def main():
    """Generate flaky test report and print it instead of sending to Slack."""
    test_data, counts = get_flaky_tests()
    text_report = format_text_report(test_data)
    print(text_report)
    # sanity check with output from:
    #
    # grep -R -E -i '(@flaky|@.*![unittest].*skip($|\())' tests
    # --exclude-dir=tests/contrib/pytest | awk '{split($0, parts, ":");
    # print parts[1]}' | sort | uniq -c | sort -r | awk '{printf("%d\t", $1);
    # system("codeowners "$2)}'
    #
    # print(f"total: {str(counts["total"])}")
    # print(counts)


if __name__ == "__main__":
    main()
