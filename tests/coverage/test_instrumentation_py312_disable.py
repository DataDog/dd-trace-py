"""Tests for Python 3.12+ coverage routed through the monitoring multiplexer."""

import sys

import pytest


@pytest.fixture(autouse=True)
def _restore_coverage_state():
    """Snapshot and restore the coverage module's shared state around each test."""
    if sys.version_info < (3, 12):
        yield
        return

    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    orig_hooks = [(code, m._CODE_HOOKS[code]) for code in m._CODE_HOOKS]
    orig_seen = [(code, set(m._seen_event_locations[code])) for code in m._seen_event_locations]
    orig_single_subscriber_version = m._single_subscriber_version
    orig_warned = m._warned_tool_unavailable

    try:
        yield
    finally:
        m._CODE_HOOKS.clear()
        for code, hook_data in orig_hooks:
            m._CODE_HOOKS[code] = hook_data
        with m._rearm_lock:
            m._seen_event_locations.clear()
            for code, locations in orig_seen:
                m._seen_event_locations[code] = locations
        m._single_subscriber_version = orig_single_subscriber_version
        m._warned_tool_unavailable = orig_warned


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
    assert m._seen_event_locations.get(code_obj) == {1}


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
    assert m._seen_event_locations.get(code_obj) == {m._FILE_EVENT_LOCATION}


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_handlers_return_disable_for_missing_code():
    """Both handlers return DISABLE for an unregistered code object (graceful handling)."""
    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    code_obj = compile("y = 2", "<test_missing>", "exec")

    assert m._CoverageLineHandler().on_py_line(code_obj, 1) is sys.monitoring.DISABLE
    assert m._CoverageFileHandler().on_py_start(code_obj, 0) is sys.monitoring.DISABLE
    assert code_obj not in m._seen_event_locations


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
@pytest.mark.parametrize("handler_name", ["_CoverageLineHandler", "_CoverageFileHandler"])
def test_handlers_isolate_structurally_equal_code_objects(handler_name, monkeypatch):
    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    code_a = compile("x = 1", "<same>", "exec")
    code_b = compile("x = 1", "<same>", "exec")
    assert code_a is not code_b
    assert code_a == code_b

    calls_a = []
    calls_b = []
    m._CODE_HOOKS[code_a] = (lambda info: calls_a.append(info), "/a.py", {}, None, None, None)
    m._CODE_HOOKS[code_b] = (lambda info: calls_b.append(info), "/b.py", {}, None, None, None)

    handler = getattr(m, handler_name)()
    if handler_name == "_CoverageLineHandler":
        handler.on_py_line(code_a, 1)
        handler.on_py_line(code_b, 1)
        expected_a = [(1, "/a.py", None)]
        expected_b = [(1, "/b.py", None)]
    else:
        handler.on_py_start(code_a, 0)
        handler.on_py_start(code_b, 0)
        expected_a = [(0, "/a.py", None)]
        expected_b = [(0, "/b.py", None)]

    assert calls_a == expected_a
    assert calls_b == expected_b
    assert sorted(id(code) for code in m._seen_event_locations) == sorted((id(code_a), id(code_b)))

    refreshed = []
    monkeypatch.setattr(m._monitoring, "restart_events", lambda _handler: None)
    monkeypatch.setattr(m._monitoring, "refresh", lambda code, events: refreshed.append((code, events)))
    m._rearm_disabled()
    assert sorted(id(code) for code, _events in refreshed) == sorted((id(code_a), id(code_b)))
    assert all(events == m._EVENT for _code, events in refreshed)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_line_handler_deduplicates_when_another_handler_keeps_event_enabled():
    from ddtrace.internal import monitoring
    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    class PassiveHandler(monitoring.MonitoringEventHandler):
        def __init__(self):
            self.calls = 0

        def on_py_line(self, code, line_number):
            self.calls += 1
            return None

    code_obj = compile("x = 1", "<overlap>", "exec")
    coverage_calls = []
    m._CODE_HOOKS[code_obj] = (lambda info: coverage_calls.append(info), "/test.py", {}, None, None, None)
    coverage_handler = m._CoverageLineHandler()
    passive_handler = PassiveHandler()
    monitoring.register(code_obj, coverage_handler)
    monitoring.register(code_obj, passive_handler)
    try:
        assert monitoring._on_py_line(code_obj, 1) is not sys.monitoring.DISABLE
        assert monitoring._on_py_line(code_obj, 1) is not sys.monitoring.DISABLE
    finally:
        monitoring.unregister(code_obj, passive_handler)
        monitoring.unregister(code_obj, coverage_handler)

    assert coverage_calls == [(1, "/test.py", None)]
    assert passive_handler.calls == 2


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_rearm_disabled_refreshes_each_touched_code_object(monkeypatch):
    """_rearm_disabled() calls monitoring.refresh() for every DISABLE'd code object and clears the set."""
    from ddtrace.internal import monitoring
    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    refreshed = []
    monkeypatch.setattr(monitoring, "restart_events", lambda _handler: None)
    monkeypatch.setattr(monitoring, "refresh", lambda code, events: refreshed.append((code, events)))

    code_a = compile("a = 1", "<a>", "exec")
    code_b = compile("b = 2", "<b>", "exec")
    assert m._claim_event(code_a, 1)
    assert m._claim_event(code_b, 2)

    m._rearm_disabled()

    assert sorted(id(code) for code, _events in refreshed) == sorted((id(code_a), id(code_b)))
    assert all(events == m._EVENT for _code, events in refreshed)
    assert len(m._seen_event_locations) == 0
    # A second call with nothing disabled is a no-op.
    refreshed.clear()
    m._rearm_disabled()
    assert refreshed == []


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_rearm_disabled_is_noop_when_empty(monkeypatch):
    from ddtrace.internal import monitoring
    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    called = []
    monkeypatch.setattr(monitoring, "restart_events", lambda _handler: None)
    monkeypatch.setattr(monitoring, "refresh", lambda code, events: called.append((code, events)))

    m._rearm_disabled()
    assert called == []


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_rearm_disabled_uses_global_restart_for_single_subscriber(monkeypatch):
    from ddtrace.internal import monitoring
    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    restarted = []

    def restart_events(handler):
        restarted.append(handler)
        return 42

    monkeypatch.setattr(monitoring, "restart_events", restart_events)
    monkeypatch.setattr(
        monitoring,
        "refresh",
        lambda code, events: pytest.fail("single-subscriber coverage must not use targeted refresh"),
    )

    code_obj = compile("a = 1", "<a>", "exec")
    m._single_subscriber_version = None
    assert m._claim_event(code_obj, 1)

    m._rearm_disabled()

    assert restarted == [m._handler]
    assert m._single_subscriber_version == 42
    assert len(m._seen_event_locations) == 0


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_claim_event_skips_software_deduplication_for_single_subscriber(monkeypatch):
    from ddtrace.internal import monitoring
    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    monkeypatch.setattr(monitoring, "subscriber_version_is_current", lambda version: version == 42)
    m._single_subscriber_version = 42
    code_obj = compile("a = 1", "<a>", "exec")

    assert m._claim_event(code_obj, 1)
    assert m._claim_event(code_obj, 1)
    assert len(m._seen_event_locations) == 0


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_rearm_disabled_refreshes_all_code_after_single_subscriber_state_ends(monkeypatch):
    from ddtrace.internal import monitoring
    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    code_a = compile("a = 1", "<a>", "exec")
    code_b = compile("b = 2", "<b>", "exec")
    hook_data = (lambda info: None, "/test.py", {}, None, None, None)
    m._CODE_HOOKS[code_a] = hook_data
    m._CODE_HOOKS[code_b] = hook_data
    m._single_subscriber_version = 42

    refreshed = []
    monkeypatch.setattr(monitoring, "restart_events", lambda _handler: None)
    monkeypatch.setattr(monitoring, "refresh", lambda code, events: refreshed.append((code, events)))

    m._rearm_disabled()

    assert sorted(id(code) for code, _events in refreshed) == sorted((id(code_a), id(code_b)))
    assert all(events == m._EVENT for _code, events in refreshed)
    assert m._single_subscriber_version is None


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_instrument_all_lines_retries_after_multiplexer_unavailable(monkeypatch):
    """A transient slot clash skips one module without permanently disabling coverage."""
    from ddtrace.internal import monitoring
    from ddtrace.internal.coverage.coverage_lines import CoverageLines
    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    attempts = 0

    def ensure_tool():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise monitoring.MonitoringToolUnavailable
        return 4

    instrumented_lines = CoverageLines()
    instrumented_lines.add(1)
    monkeypatch.setattr(m._monitoring, "ensure_tool", ensure_tool)
    monkeypatch.setattr(
        m,
        "_instrument_with_monitoring",
        lambda code, hook, path, package: (code, instrumented_lines),
    )

    code_obj = compile("x = 1", "<test_degrade>", "exec")
    first_code, first_lines = m.instrument_all_lines(code_obj, lambda info: None, "/test/path.py", "pkg")
    second_code, second_lines = m.instrument_all_lines(code_obj, lambda info: None, "/test/path.py", "pkg")

    assert first_code is code_obj
    assert len(first_lines) == 0
    assert second_code is code_obj
    assert second_lines is instrumented_lines
    assert attempts == 2
