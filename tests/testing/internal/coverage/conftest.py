import os


def pytest_configure(config):
    # These tests exercise coverage.py start/stop/report directly. If the Test
    # Optimization plugin has also started a global coverage.py instance (e.g.
    # when DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED is set), the
    # tests will pick up that instance and corrupt session-level coverage state.
    # Disable report upload before the plugin's pytest_configure runs so it
    # never starts its own instance.
    os.environ["DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED"] = "false"
