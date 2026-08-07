from unittest.mock import Mock

from ddtrace.appsec._iast._pytest_plugin import VulnerabilityFoundInTest
from ddtrace.appsec._iast._pytest_plugin import print_iast_report
from ddtrace.appsec._iast._pytest_plugin import vuln_data
from tests.utils import override_global_config


def test_print_iast_report_uses_serialized_vulnerability(tmp_path):
    source_file = tmp_path / "source.py"
    source_file.write_text("vulnerable()\n")
    vulnerability = VulnerabilityFoundInTest(
        type="COMMAND_INJECTION",
        evidence={"value": "vulnerable"},
        location={"path": str(source_file), "line": 1},
        test="test_vulnerable",
    )
    terminal_reporter = Mock()

    vuln_data.append(vulnerability)
    try:
        with override_global_config(dict(_iast_enabled=True)):
            print_iast_report(terminal_reporter)
    finally:
        vuln_data.clear()

    terminal_reporter.write_line.assert_any_call("Test: test_vulnerable", bold=True)
    terminal_reporter.write_line.assert_any_call(f"Location: {source_file}:1")
    terminal_reporter.write.assert_called_once_with("   1: vulnerable()\n", bold=True, purple=True)
