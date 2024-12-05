#!/usr/bin/env python3
from typing import List
import json

import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._utils import _is_iast_enabled


vuln_data: List[dict] = []
DD_DOCS = "https://docs.datadoghq.com"

remediation = {
    "SQL_INJECTION": f"{DD_DOCS}/code_analysis/static_analysis_rules/python-flask/sqlalchemy-injection/",
    "WEAK_HASH": "f{DD_DOCS}/code_analysis/static_analysis_rules/python-security/insecure-hash-functions/",
}


def extract_code_snippet(filepath, line_number, context=3):
    """Extracts code snippet around the given line number."""
    try:
        with open(filepath, "r") as file:
            lines = file.readlines()
            start = max(0, line_number - context - 1)
            end = min(len(lines), line_number + context)
            code = lines[start:end]
            return code, start  # Return lines and starting line number
    except Exception as e:
        return [f"Error reading file {filepath}: {e}"], None


def print_iast_report(terminalreporter):
    if not _is_iast_enabled():
        return

    terminalreporter.write("\nDatadog Code Security Report:\n", bold=True, purple=True)

    if vuln_data:
        terminalreporter.write(f"{'=' * 80}\n")

        for entry in vuln_data:
            terminalreporter.write(f"Test: {entry['nodeid']}\n", bold=True)
            critical = entry["vulnerability"] == "SQL_INJECTION"
            terminalreporter.write(
                f"Vulnerability: {entry['vulnerability']} - \033]8;;"
                f"{remediation[entry['vulnerability']]}\033\\Remediation\033]8;;\033\\ \n",
                bold=True,
                red=critical,
                yellow=not critical,
            )
            terminalreporter.write(f"Location: {entry['file']}:{entry['line']}\n")
            terminalreporter.write("Code:\n")
            code_snippet, start_line = extract_code_snippet(entry["file"], entry["line"])

            if start_line is not None:
                for i, line in enumerate(code_snippet, start=start_line + 1):
                    if i == entry["line"]:
                        terminalreporter.write(f"{i:4d}: {line}", bold=True, purple=True)
                    else:
                        terminalreporter.write(f"{i:4d}: {line}")
            else:
                # If there's an error extracting the code snippet
                terminalreporter.write(code_snippet[0] + "\n", bold=True)

            terminalreporter.write(f"{'=' * 80}\n")

    else:
        terminalreporter.write("\nNo vulnerabilities found.\n")


@pytest.fixture(autouse=_is_iast_enabled())
def ddtrace_iast(request, ddspan):
    """Return the :class:`ddtrace._trace.span.Span` instance associated with the
    current test when Datadog CI Visibility is enabled.
    """
    yield
    data = ddspan.get_tag(IAST.JSON)
    if data:
        json_data = json.loads(data)

        if json_data["vulnerabilities"]:
            for vuln in json_data["vulnerabilities"]:
                vuln_data.append(
                    {
                        "nodeid": request.node.nodeid,
                        "vulnerability": vuln["type"],
                        "file": vuln["location"]["path"],
                        "line": vuln["location"]["line"],
                    }
                )
            if request.config.getoption("ddtrace-iast-fail-tests"):
                vulns = ", ".join([vuln["type"] for vuln in json_data["vulnerabilities"]])
                pytest.fail(f"There are vulnerabilities in the code: {vulns}")
