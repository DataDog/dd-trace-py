"""Log pytest observations without adding Test Visibility sessions or JUnit reports."""

from collections import Counter
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import time
from typing import Any
from typing import Iterator
from typing import Optional

import pytest


_STARTED = time.monotonic()


def _installed_version(name: str) -> Optional[str]:
    """Do not abort test collection when an optional pytest plugin is absent."""
    try:
        return version(name)
    except PackageNotFoundError:
        return None


class TestmonLogging:
    def __init__(self, config: Any) -> None:
        self.config = config
        self.worker = hasattr(config, "workerinput")
        self.selected: set[str] = set()
        self.deselected: set[str] = set()
        self.deselection_notifications_without_nodeid = 0
        self.failed: set[str] = set()
        self.skipped: set[str] = set()
        self.collection_errors = 0
        self.outcomes: Counter[str] = Counter()
        self.collection_seconds: Optional[float] = None
        self.startup_selection_seconds: Optional[float] = None
        self.worker_collection_seconds: dict[str, Optional[float]] = {}
        self.test_report_seconds = 0.0

    def log(self, event: str, **fields: Any) -> None:
        if not self.worker:
            reporter = self.config.pluginmanager.getplugin("terminalreporter")
            reporter.write_line(
                "[TIA] "
                + json.dumps(
                    {
                        "event": event,
                        "environment": os.environ.get("DD_LLMOBS_TIA_ENVIRONMENT"),
                        "phase": os.environ.get("DD_LLMOBS_TIA_PHASE", "normal"),
                        **fields,
                    },
                    sort_keys=True,
                )
            )

    def pytest_sessionstart(self, session: Any) -> None:
        self.log(
            "pytest_configuration",
            python=platform.python_version(),
            packages={
                name: _installed_version(name) for name in ("pytest", "pytest-testmon", "coverage", "pytest-xdist")
            },
            workers=self.config.getoption("numprocesses", default=0),
            distribution=self.config.getoption("dist", default="no"),
            retry_environment={
                key: os.environ.get(key)
                for key in (
                    "DD_CIVISIBILITY_FLAKY_RETRY_ENABLED",
                    "DD_CIVISIBILITY_DYNAMIC_ATR_ENABLED",
                    "DD_TEST_VISIBILITY_EARLY_FLAKE_DETECTION_ENABLED",
                )
            },
        )

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)  # type: ignore[misc]
    def pytest_collection(self, session: Any) -> Iterator[None]:
        started = time.monotonic()
        yield
        self.collection_seconds = time.monotonic() - started
        self.startup_selection_seconds = time.monotonic() - _STARTED
        # An xdist controller can finish this hook without collecting local items.
        self.selected.update(item.nodeid for item in getattr(session, "items", ()))

    def pytest_collectreport(self, report: Any) -> None:
        if report.failed:
            self.collection_errors += 1

    def pytest_deselected(self, items: Any) -> None:
        for item in items:
            nodeid = getattr(item, "nodeid", None)
            if nodeid is None:
                # Testmon can report synthetic deselections without a test ID.
                self.deselection_notifications_without_nodeid += 1
            else:
                self.deselected.add(nodeid)

    @pytest.hookimpl(optionalhook=True)  # type: ignore[misc]
    def pytest_xdist_node_collection_finished(self, node: Any, ids: Any) -> None:
        self.selected.update(ids)
        self.collection_seconds = None
        self.startup_selection_seconds = time.monotonic() - _STARTED

    @pytest.hookimpl(optionalhook=True)  # type: ignore[misc]
    def pytest_testnodedown(self, node: Any, error: Any) -> None:
        data = node.workeroutput.get("tia", {})
        self.deselected.update(data.get("deselected", []))
        self.deselection_notifications_without_nodeid += data.get("deselection_notifications_without_nodeid", 0)
        self.worker_collection_seconds[node.gateway.id] = data.get("collection_seconds")

    def pytest_runtest_logreport(self, report: Any) -> None:
        self.test_report_seconds += report.duration
        if report.when == "call" or report.failed or report.skipped:
            self.outcomes[f"{report.when}_{report.outcome}"] += 1
        if report.failed:
            self.failed.add(report.nodeid)
        if report.skipped:
            self.skipped.add(report.nodeid)
            self.log("runtime_skip", test=report.nodeid, stage=report.when, reason=str(report.longrepr))

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)  # type: ignore[misc]
    def pytest_sessionfinish(self, session: Any, exitstatus: Any) -> Iterator[None]:
        data = {
            "selected": sorted(self.selected),
            "deselected": sorted(self.deselected),
            "deselection_notifications_without_nodeid": self.deselection_notifications_without_nodeid,
            "failed": sorted(self.failed),
            "skipped": sorted(self.skipped),
            "outcomes": dict(self.outcomes),
            "collection_errors": self.collection_errors,
            "collection_seconds": self.collection_seconds,
        }
        if self.worker:
            # xdist sends workeroutput during sessionfinish; populate it before yielding.
            self.config.workeroutput["tia"] = data
        yield
        if self.worker:
            return
        self.log(
            "pytest_finish",
            exit_code=int(exitstatus),
            selected_count=len(self.selected),
            observed_deselected=sorted(self.deselected),
            deselection_notifications_without_nodeid=self.deselection_notifications_without_nodeid,
            outcomes=dict(self.outcomes),
            collection_errors=self.collection_errors,
            collection_seconds=self.collection_seconds,
            worker_collection_seconds=self.worker_collection_seconds,
            startup_and_selection_seconds=self.startup_selection_seconds,
            summed_test_report_seconds=self.test_report_seconds,
            scope=(
                "deselections may omit whole files; unidentified notifications include worker duplicates; "
                "startup timing starts at plugin import and includes collection"
            ),
        )
        output = os.environ.get("DD_LLMOBS_TIA_INVENTORY")
        if output:
            Path(output).write_text(json.dumps(data))


def pytest_configure(config: Any) -> None:
    config.pluginmanager.register(TestmonLogging(config), "testmon-logging")
