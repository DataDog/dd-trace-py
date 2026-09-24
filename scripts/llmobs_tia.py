"""Pytest measurements for the LLMObs testmon experiment branch."""

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
            "mode": "testmon",
            "database_reused": os.environ.get("DD_LLMOBS_TIA_DATABASE_REUSED") == "1",
            "pytest_version": version("pytest"),
            "coverage_version": version("coverage"),
            "python_version": sys.version,
            "testmon_version": version("pytest-testmon"),
        }
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

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node, ids):
        # Workers collect concurrently; controller collection time is not their
        # collection time. Record elapsed startup through the last worker instead.
        self.metrics["collection_seconds"] = None
        self.metrics["startup_and_selection_seconds"] = time.monotonic() - _STARTED
        self.metrics["selected_tests"] = len(ids)
        self.metrics["deselected_tests"] = None

    def pytest_runtest_logreport(self, report):
        if report.when == "call" or report.failed or report.skipped:
            self.outcomes[f"{report.when}_{report.outcome}"] += 1

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_sessionfinish(self, session, exitstatus):
        yield
        # A warm selector can legitimately leave no work. Keep cold empty suites,
        # collection errors, and all other pytest failures visible to CI.
        if (
            self.metrics["database_reused"]
            and exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED
            and session.testscollected == 0
            and session.testsfailed == 0
        ):
            session.exitstatus = pytest.ExitCode.OK
        self.metrics.update(
            pytest_exit_code=int(exitstatus),
            deselected_tests=self.metrics.get("deselected_tests", self.deselected),
            outcomes=dict(self.outcomes),
            session_seconds=time.monotonic() - _STARTED,
        )
        Path(os.environ["DD_LLMOBS_TIA_REPORT"]).write_text(json.dumps(self.metrics, indent=2) + "\n")


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    # The controller receives worker results and owns the single report file.
    if not hasattr(config, "workerinput"):
        config.pluginmanager.register(Measurements(), "llmobs-tia-measurements")
