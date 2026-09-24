"""Pytest measurements for the opt-in LLMObs TIA experiment."""

from collections import Counter
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
import time

import pytest


_STARTED = time.monotonic()


class Measurements:
    def __init__(self):
        self.metrics = {
            "mode": os.environ["DD_LLMOBS_TIA_MODE"],
            "database_reused": os.environ.get("DD_LLMOBS_TIA_DATABASE_REUSED") == "1",
            "pytest_version": version("pytest"),
            "coverage_version": version("coverage"),
            "python_version": sys.version,
        }
        if self.metrics["mode"] in ("collect", "testmon"):
            self.metrics["testmon_version"] = version("pytest-testmon")
        self.outcomes = Counter()
        self.deselected = 0

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_collection(self, session):
        started = time.monotonic()
        yield
        self.metrics["collection_seconds"] = time.monotonic() - started
        self.metrics["startup_and_selection_seconds"] = time.monotonic() - _STARTED
        self.metrics["selected_tests"] = session.testscollected

    def pytest_deselected(self, items):
        self.deselected += len(items)

    def pytest_runtest_logreport(self, report):
        if report.when == "call" or report.failed or report.skipped:
            self.outcomes[f"{report.when}_{report.outcome}"] += 1

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_sessionfinish(self, session, exitstatus):
        yield
        # A warm selector can legitimately leave no work. Keep cold empty suites,
        # collection errors, and all other pytest failures visible to CI.
        if (
            self.metrics["mode"] == "testmon"
            and self.metrics["database_reused"]
            and exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED
            and session.testscollected == 0
            and session.testsfailed == 0
        ):
            session.exitstatus = pytest.ExitCode.OK
        self.metrics.update(
            pytest_exit_code=int(exitstatus),
            deselected_tests=self.deselected,
            outcomes=dict(self.outcomes),
            session_seconds=time.monotonic() - _STARTED,
        )
        Path(os.environ["DD_LLMOBS_TIA_REPORT"]).write_text(json.dumps(self.metrics, indent=2) + "\n")


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    config.pluginmanager.register(Measurements(), "llmobs-tia-measurements")
