#!/usr/bin/env python3
"""Add a `file` attribute to the testcases of ctest JUnit reports.

ctest's JUnit output has no source file, so Test Optimization can't set
`test.source.file` nor compute code owners. Each GoogleTest name is
`Suite.Test` (optionally `Prefix/Suite.Test/Param`): find the file that
defines `TEST*(Suite, Test)` under the given source roots.

Usage: junit-add-source-file.py <junit dir> <source root>...
"""

from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET


TEST_MACRO = re.compile(r"^\s*(?:TYPED_)?TEST(?:_F|_P)?\(\s*(\w+)\s*,\s*(\w+)\s*\)", re.MULTILINE)


def index_tests(roots):
    """Map 'Suite.Test' and 'Suite' to the repository-relative file that defines them."""
    tests, suites = {}, {}
    for root in roots:
        for path in sorted(Path(root).rglob("*.cpp")):
            for suite, test in TEST_MACRO.findall(path.read_text(errors="replace")):
                tests.setdefault(f"{suite}.{test}", str(path))
                suites.setdefault(suite, str(path))
    return tests, suites


def source_file(name, tests, suites):
    # Parameterized tests: Prefix/Suite.Test/Param -> Suite.Test
    parts = name.split("/")
    for candidate in [name] + parts:
        if candidate in tests:
            return tests[candidate]
    suite = (parts[1] if len(parts) > 1 else parts[0]).split(".")[0]
    return suites.get(suite)


def main():
    junit_dir, roots = sys.argv[1], sys.argv[2:]
    tests, suites = index_tests(roots)
    for report in sorted(Path(junit_dir).glob("*.xml")):
        tree = ET.parse(report)
        missing = 0
        for testcase in tree.iter("testcase"):
            path = source_file(testcase.get("name", ""), tests, suites)
            if path:
                testcase.set("file", path)
            else:
                missing += 1
        tree.write(report, encoding="utf-8", xml_declaration=True)
        if missing:
            print(f"{report}: no source file found for {missing} testcase(s)")


if __name__ == "__main__":
    main()
