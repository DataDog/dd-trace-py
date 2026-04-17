"""Pytest plugin for taint-based test isolation checking.

Automatically active when loaded via conftest.py pytest_plugins.
"""

from __future__ import annotations

import os

import pytest

from . import instrument
from . import taint
from .instrument import _is_internal


_enabled = os.environ.get("DD_TAINT_TRACKING_ENABLED", "1") not in ("0", "false", "no")
_last_leak_report: list[tuple[str, str]] | None = None


def pytest_configure(config: pytest.Config) -> None:
    if not _enabled:
        return
    rootdir = str(config.rootpath)
    instrument.install(test_paths=[rootdir])


def pytest_report_header() -> str:
    if not _enabled:
        return "taint-tracking: disabled"
    return "taint-tracking: active"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item: pytest.Item):
    if _enabled:
        taint.set_current_test(item.nodeid)
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item):
    global _last_leak_report
    if not _enabled:
        yield
        return
    taint.set_current_test(item.nodeid)

    outcome = yield

    excinfo = outcome.excinfo
    if excinfo is not None:
        _, _, tb = excinfo
        _last_leak_report = _scan_traceback(tb, item.nodeid)
    else:
        _last_leak_report = None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call):
    global _last_leak_report
    outcome = yield
    report = outcome.get_result()

    if report.when == "call" and report.failed and _last_leak_report:
        leak_lines = ["", "=== TAINT LEAK DETECTION ==="]
        for varname, obj_taint in _last_leak_report:
            leak_lines.append(f"  Variable '{varname}' was tainted by: {obj_taint}")
        leak_lines.append("=== END TAINT LEAK DETECTION ===")
        report.sections.append(("Taint Leak Detection", "\n".join(leak_lines)))
        _last_leak_report = None


def pytest_runtest_teardown(item: pytest.Item) -> None:
    taint.set_current_test(None)


def pytest_sessionfinish() -> None:
    instrument.uninstall()


_MAX_SCAN_DEPTH = 2


def _check_value(
    obj,
    path: str,
    current_nodeid: str,
    seen_ids: set[int],
    leaked: list[tuple[str, str]],
    depth: int,
) -> None:
    obj_id = id(obj)
    if obj_id in seen_ids:
        return
    seen_ids.add(obj_id)

    obj_taint = taint.get_taint(obj)
    if obj_taint is not None and obj_taint != current_nodeid:
        leaked.append((path, obj_taint))

    if depth >= _MAX_SCAN_DEPTH:
        return

    if isinstance(obj, dict):
        for k, v in obj.items():
            _check_value(v, f"{path}[{k!r}]", current_nodeid, seen_ids, leaked, depth + 1)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _check_value(v, f"{path}[{i}]", current_nodeid, seen_ids, leaked, depth + 1)
    elif isinstance(obj, (set, frozenset)):
        for v in obj:
            _check_value(v, f"{path}{{...}}", current_nodeid, seen_ids, leaked, depth + 1)
    elif hasattr(obj, "__dict__"):
        for attr, v in vars(obj).items():
            if not attr.startswith("_"):
                _check_value(v, f"{path}.{attr}", current_nodeid, seen_ids, leaked, depth + 1)


def _scan_traceback(tb, current_nodeid: str) -> list[tuple[str, str]]:
    leaked: list[tuple[str, str]] = []
    seen_ids: set[int] = set()
    while tb is not None:
        frame = tb.tb_frame
        filename = frame.f_code.co_filename
        if _is_internal(filename):
            tb = tb.tb_next
            continue
        for varname, value in frame.f_locals.items():
            if varname.startswith("@") or varname == instrument.TAINT_MARK_NAME:
                continue
            _check_value(value, varname, current_nodeid, seen_ids, leaked, 0)
        tb = tb.tb_next
    return leaked
