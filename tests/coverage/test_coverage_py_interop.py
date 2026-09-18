"""
Integration test: ddtrace's per-test coverage collector coexisting with a REAL
`coverage.py` instance, both riding Python 3.12+'s sys.monitoring API at the same time.

This is a regression test for the bug where ddtrace called the global
sys.monitoring.restart_events() unconditionally on every CollectInContext entry.
That call resets the disabled-event bookkeeping for *every* registered sys.monitoring
tool, not just ddtrace's -- so when a real coverage tool such as coverage.py was also
using sys.monitoring (e.g. `pytest-cov` running alongside ddtrace's CI Visibility / ITR
per-test coverage collection), ddtrace would repeatedly stomp on coverage.py's own internal
state and corrupt its report.

The fix: coverage registers a handler with the shared ddtrace sys.monitoring
multiplexer. A sole ddtrace subscriber may use global `restart_events()` only while no
external tool is visible. A registered coverage.py tool rejects that shortcut, so
per-test re-arming uses a per-(tool, code, location) `set_local_events` toggle in
`monitoring.refresh()` and cannot alter coverage.py's disabled-event state.

Unlike the other tests in this directory (test_coverage_tool_clash.py,
test_instrumentation_py312_disable.py, test_coverage_context_reinstrumentation.py),
which simulate "another tool" via a bare `sys.monitoring.use_tool_id(slot, "fake_name")`
with no registered callback, this test drives a real `coverage.Coverage()` instance so
that coverage.py's *actual* sys.monitoring callbacks -- which independently use the same
DISABLE-after-recording optimisation ddtrace uses (see coverage/sysmon.py:
sysmon_line_lines(), which unconditionally `return DISABLE` after recording a line) --
are exercised for real, at the same time as ddtrace's.
"""

import sys

import pytest


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring coverage is only used in Python 3.12+")
@pytest.mark.subprocess(env={"COVERAGE_CORE": "sysmon", "_DD_COVERAGE_FILE_LEVEL": "false"})
def test_ddtrace_context_transition_with_real_coverage_py():
    """
    Scenario (mirrors pytest + pytest-cov + ddtrace CI Visibility running together):

    1. ddtrace's ModuleCodeCollector is installed first, as it would be at pytest
       session start-up. Coverage registers with the shared sys.monitoring multiplexer
       (tool-scoped DISABLE, always on; no other tool yet).
    2. The target modules are imported (top-level, outside of any context) and one of
       them is called once -- this call's LINE event fires and is silenced (an "early
       window" hit, like real conftest-time imports before pytest-cov registers).
    3. A *real* `coverage.Coverage` is started with COVERAGE_CORE=sysmon (forced via
       the subprocess env: sys.monitoring is not coverage.py's default core before
       Python 3.14). The same line is executed again -- now observed by coverage.py,
       whose own sys.monitoring callback also returns DISABLE after recording it (on
       coverage.py's own, separate tool slot).
    4. A ddtrace CollectInContext is entered. Because coverage.py's tool is visible,
       _rearm_disabled() rejects the global shortcut and re-arms ddtrace's own events
       via monitoring.refresh(). A different line is then executed for the first time.
    5. A second CollectInContext is entered/exited. coverage.py remains visible, so
       per-test coverage isolation continues through the selective fallback.

    Assertions verify BOTH tools end up with correct, uncorrupted data: ddtrace's
    per-context coverage is complete and properly isolated between the two contexts,
    and coverage.py's own analysis2() shows both the pre-transition line (step 2/3) and
    the post-transition line (step 4) as covered -- i.e. ddtrace's tool-scoped re-arm
    never erased coverage.py's data, and coverage.py kept working correctly afterwards.
    """
    import os
    from pathlib import Path
    import sys

    import coverage

    from ddtrace.internal import monitoring
    from ddtrace.internal.coverage import instrumentation_py3_12
    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path) / "tests" / "coverage" / "included_path"

    # Step 1: install ddtrace's collector first, as it would be at pytest session
    # start (before pytest-cov / coverage.py has had a chance to register its own
    # sys.monitoring tool in pytest_configure).
    install(include_paths=[include_path])

    from tests.coverage.included_path.in_context_lib import called_in_context
    from tests.coverage.included_path.lib import called_in_session

    lib_path = str(include_path / "lib.py")
    in_context_lib_path = str(include_path / "in_context_lib.py")

    # Step 2: execute code in the "early window" -- before coverage.py exists at all.
    # ddtrace's own DISABLE optimisation (still True, the default) silences this
    # line's event on ddtrace's tool slot.
    called_in_session(0, 1)

    # Step 3: start a REAL coverage.py using the sys.monitoring ("sysmon") core.
    # data_file=None/config_file=False keep this hermetic: no .coverage file is
    # written, and the repo's own .coveragerc is ignored.
    cov = coverage.Coverage(data_file=None, config_file=False)
    cov.start()

    assert any(sys.monitoring.get_tool(tool_id) not in (None, "ddtrace") for tool_id in range(6)), (
        "coverage.py must own a separate sys.monitoring slot"
    )
    assert monitoring.restart_events(instrumentation_py3_12._handler) is None

    try:
        # Same line, now observed by coverage.py for the first time. coverage.py
        # records it and DISABLEs that line's event on its own tool slot -- this is
        # the "other tool has already DISABLE'd some of its own lines" precondition
        # that must hold true when ddtrace's transition fires below.
        called_in_session(2, 3)

        # Step 4: entering this context re-arms ddtrace's own silenced events via the
        # tool-scoped _rearm_disabled() (monitoring.refresh()), which cannot
        # touch coverage.py's separate tool slot.
        with ModuleCodeCollector.CollectInContext() as ctx1:
            # Re-executing the same call proves ddtrace's tool-scoped re-arm
            # did not corrupt coverage.py's already-recorded lib.py:2 data point,
            # and that ddtrace's own context-scoped tracking records it correctly
            # once its event is re-armed.
            called_in_session(4, 5)
            # A different line, executed here for the very first time -- the
            # "recorded strictly after ddtrace's re-arm point" data point.
            called_in_context(6, 7)
            ctx1_covered = _get_relpath_dict(cwd_path, ctx1.get_covered_lines())

        # Step 5: coverage.py remains visible, so this context also takes the
        # selective fallback and preserves per-test coverage isolation.
        with ModuleCodeCollector.CollectInContext() as ctx2:
            called_in_context(8, 9)
            ctx2_covered = _get_relpath_dict(cwd_path, ctx2.get_covered_lines())
    finally:
        cov.stop()

    # --- ddtrace assertions: per-context coverage is complete and isolated ---
    expected_ctx1 = {
        "tests/coverage/included_path/lib.py": {2},
        "tests/coverage/included_path/in_context_lib.py": {2},
    }
    expected_ctx2 = {
        "tests/coverage/included_path/in_context_lib.py": {2},
    }
    assert ctx1_covered == expected_ctx1, f"ctx1 coverage mismatch: expected={expected_ctx1} vs actual={ctx1_covered}"
    assert ctx2_covered == expected_ctx2, f"ctx2 coverage mismatch: expected={expected_ctx2} vs actual={ctx2_covered}"

    # --- coverage.py assertions: its own report must be correct, not corrupted by
    # ddtrace's tool-scoped re-arm (which never touches coverage.py's tool slot) ---
    _, lib_statements, _, lib_missing, _ = cov.analysis2(lib_path)
    _, ctxlib_statements, _, ctxlib_missing, _ = cov.analysis2(in_context_lib_path)

    assert 2 in lib_statements, f"lib.py line 2 should be executable, got statements={lib_statements}"
    assert 2 not in lib_missing, (
        f"coverage.py failed to keep recording lib.py line 2 (executed BEFORE the "
        f"ddtrace re-arm point, then again inside ctx1) -- missing={lib_missing}"
    )

    assert 2 in ctxlib_statements, f"in_context_lib.py line 2 should be executable, got statements={ctxlib_statements}"
    assert 2 not in ctxlib_missing, (
        f"coverage.py failed to record in_context_lib.py line 2 (executed strictly "
        f"AFTER the ddtrace re-arm point) -- missing={ctxlib_missing}"
    )
