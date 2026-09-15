"""
Unit tests for Python 3.12+ coverage instrumentation routed through the sys.monitoring
multiplexer.

Coverage no longer claims its own sys.monitoring tool slot; it registers a single
shared :class:`~ddtrace.internal.monitoring.MonitoringEventHandler` with the
multiplexer. The handler always returns ``sys.monitoring.DISABLE`` after recording
(performance: each line/file fires once per context), and per-test re-arming is
tool-scoped via ``monitoring.refresh()`` -- never the global ``restart_events()``,
which is what makes coexistence with other tools (internal or external) safe.

These tests exercise the handler dispatch logic and the graceful-degradation
flag directly. End-to-end re-arm and tool-clash behaviour is covered by
``test_coverage_context_reinstrumentation.py`` and ``test_coverage_tool_clash.py``.
"""

import sys

import pytest


@pytest.fixture(autouse=True)
def _restore_coverage_state():
    """Snapshot and restore the coverage module's shared state around each test."""
    if sys.version_info < (3, 12):
        yield
        return

    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    orig_hooks = dict(m._CODE_HOOKS)
    orig_disabled = set(m._disabled_code)
    orig_unavailable = m._multiplexer_unavailable

    try:
        yield
    finally:
        # _CODE_HOOKS is restored in place because the live plugin holds a reference to
        # this same dict object.
        m._CODE_HOOKS.clear()
        m._CODE_HOOKS.update(orig_hooks)
        with m._rearm_lock:
            m._disabled_code.clear()
            m._disabled_code.update(orig_disabled)
        m._multiplexer_unavailable = orig_unavailable


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_line_handler_returns_disable_and_records():
    """The line handler returns DISABLE after recording a line (perf: fire once per context)."""
    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    code_obj = compile("x = 1", "<test>", "exec")
    calls = []
    m._CODE_HOOKS[code_obj] = (lambda info: calls.append(info), "/test/path.py", {}, None, None, None)

    handler = m._CoverageLineHandler()
    result = handler.on_py_line(code_obj, 1)

    assert result is sys.monitoring.DISABLE
    assert calls == [(1, "/test/path.py", None)]
    assert code_obj in m._disabled_code


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_file_handler_returns_disable_and_records():
    """The file handler returns DISABLE after recording file coverage via PY_START."""
    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    code_obj = compile("x = 1", "<test_file>", "exec")
    calls = []
    m._CODE_HOOKS[code_obj] = (lambda info: calls.append(info), "/test/path.py", {}, None, None, None)

    handler = m._CoverageFileHandler()
    result = handler.on_py_start(code_obj, 0)

    assert result is sys.monitoring.DISABLE
    # File-level coverage reports line 0.
    assert calls == [(0, "/test/path.py", None)]
    assert code_obj in m._disabled_code


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_handlers_return_disable_for_missing_code():
    """Both handlers return DISABLE for an unregistered code object (graceful handling)."""
    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    code_obj = compile("y = 2", "<test_missing>", "exec")

    assert m._CoverageLineHandler().on_py_line(code_obj, 1) is sys.monitoring.DISABLE
    assert m._CoverageFileHandler().on_py_start(code_obj, 0) is sys.monitoring.DISABLE
    # Missing code must not be added to the re-arm set.
    assert code_obj not in m._disabled_code


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_line_handler_uses_specialized_line_and_import_hooks():
    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    code_obj = compile("x = 1", "<test_line_hooks>", "exec")
    generic_calls = []
    line_calls = []
    file_calls = []
    import_calls = []
    import_name = ("tests.coverage", ("included_path",))

    m._CODE_HOOKS[code_obj] = (
        lambda info: generic_calls.append(info),
        "/test/path.py",
        {7: import_name},
        lambda path, line: line_calls.append((path, line)),
        lambda path: file_calls.append(path),
        lambda path, name: import_calls.append((path, name)),
    )

    handler = m._CoverageLineHandler()
    assert handler.on_py_line(code_obj, 7) is sys.monitoring.DISABLE

    assert generic_calls == []
    assert line_calls == [("/test/path.py", 7)]
    assert file_calls == []
    assert import_calls == [("/test/path.py", import_name)]


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_file_handler_uses_specialized_file_and_import_hooks():
    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    code_obj = compile("x = 1", "<test_file_hooks>", "exec")
    generic_calls = []
    file_calls = []
    import_calls = []
    import_name = ("tests.coverage", ("included_path",))

    m._CODE_HOOKS[code_obj] = (
        lambda info: generic_calls.append(info),
        "/test/path.py",
        {10: import_name},
        lambda path, line: None,
        lambda path: file_calls.append(path),
        lambda path, name: import_calls.append((path, name)),
    )

    handler = m._CoverageFileHandler()
    assert handler.on_py_start(code_obj, 0) is sys.monitoring.DISABLE

    assert generic_calls == []
    assert file_calls == ["/test/path.py"]
    assert import_calls == [("/test/path.py", import_name)]


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_rearm_disabled_refreshes_each_touched_code_object(monkeypatch):
    """_rearm_disabled() calls monitoring.refresh() for every DISABLE'd code object and clears the set."""
    from ddtrace.internal import monitoring
    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    refreshed = []
    monkeypatch.setattr(monitoring, "refresh", lambda code: refreshed.append(code))

    code_a = compile("a = 1", "<a>", "exec")
    code_b = compile("b = 2", "<b>", "exec")
    with m._rearm_lock:
        m._disabled_code.add(code_a)
        m._disabled_code.add(code_b)

    m._rearm_disabled()

    assert set(refreshed) == {code_a, code_b}
    assert len(m._disabled_code) == 0
    # A second call with nothing disabled is a no-op.
    refreshed.clear()
    m._rearm_disabled()
    assert refreshed == []


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_rearm_disabled_is_noop_when_empty(monkeypatch):
    from ddtrace.internal import monitoring
    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    called = []
    monkeypatch.setattr(monitoring, "refresh", lambda code: called.append(code))

    m._rearm_disabled()
    assert called == []


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_instrument_all_lines_degrades_when_multiplexer_unavailable():
    """When the multiplexer has no tool slot, instrument_all_lines() is a no-op returning empty lines."""
    import ddtrace.internal.coverage.instrumentation_py3_12 as m
    from ddtrace.internal.test_visibility.coverage_lines import CoverageLines

    m._multiplexer_unavailable = True

    code_obj = compile("x = 1", "<test_degrade>", "exec")
    new_code, lines = m.instrument_all_lines(code_obj, lambda info: None, "/test/path.py", "pkg")

    # No instrumentation, no lines, code object unchanged.
    assert new_code is code_obj
    assert isinstance(lines, CoverageLines) and len(lines) == 0
    assert code_obj not in m._CODE_HOOKS
