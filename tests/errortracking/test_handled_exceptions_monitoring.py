import sys

import pytest


pytestmark = pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring requires Python 3.12+")


@pytest.mark.subprocess(out=None, err=None)
def test_handled_exception_reporting_uses_shared_monitoring_tool():
    import sys

    sys.monitoring.use_tool_id(4, "external-4")

    from ddtrace.errortracking._handled_exceptions.monitoring_reporting import _install_sys_monitoring_reporting
    from ddtrace.errortracking._handled_exceptions.monitoring_reporting import _uninstall_sys_monitoring_reporting
    from ddtrace.internal import monitoring

    _install_sys_monitoring_reporting()
    tool_id = monitoring._tool_id
    assert tool_id == 3
    assert sys.monitoring.get_tool(4) == "external-4"
    assert sys.monitoring.get_tool(tool_id) == "ddtrace"
    assert sys.monitoring.get_events(tool_id) & sys.monitoring.events.EXCEPTION_HANDLED

    class LineHandler(monitoring.MonitoringEventHandler):
        def __init__(self):
            self.lines = []

        def on_py_line(self, code, line_number):
            self.lines.append(line_number)

    def target():
        pass

    line_handler = LineHandler()
    monitoring.register(target.__code__, line_handler)
    assert sys.monitoring.get_local_events(tool_id, target.__code__) & sys.monitoring.events.LINE

    _uninstall_sys_monitoring_reporting()
    assert sys.monitoring.get_tool(tool_id) == "ddtrace"
    assert not (sys.monitoring.get_events(tool_id) & sys.monitoring.events.EXCEPTION_HANDLED)
    assert sys.monitoring.get_local_events(tool_id, target.__code__) & sys.monitoring.events.LINE

    target()
    assert line_handler.lines
    monitoring.unregister(target.__code__, line_handler)


@pytest.mark.subprocess(out=None, err=None)
def test_handled_exception_uninstall_releases_last_tool_registration():
    import sys
    from types import CodeType

    from ddtrace.errortracking._handled_exceptions.monitoring_reporting import _install_sys_monitoring_reporting
    from ddtrace.errortracking._handled_exceptions.monitoring_reporting import _uninstall_sys_monitoring_reporting
    from ddtrace.internal import monitoring

    sys_monitoring = getattr(sys, "monitoring")
    _install_sys_monitoring_reporting()
    tool_id = monitoring._tool_id
    assert tool_id == 3

    _uninstall_sys_monitoring_reporting()

    assert monitoring._tool_id is None
    assert sys_monitoring.get_tool(tool_id) is None
    assert sys_monitoring.get_events(tool_id) == 0

    sys_monitoring.use_tool_id(tool_id, "external")

    def callback(code: CodeType, instruction_offset: int, exception: BaseException) -> None:
        pass

    assert sys_monitoring.register_callback(tool_id, sys_monitoring.events.EXCEPTION_HANDLED, callback) is None
    sys_monitoring.register_callback(tool_id, sys_monitoring.events.EXCEPTION_HANDLED, None)
    sys_monitoring.free_tool_id(tool_id)

    _install_sys_monitoring_reporting()
    assert monitoring._tool_id == tool_id
    _uninstall_sys_monitoring_reporting()
    assert monitoring._tool_id is None


@pytest.mark.subprocess(out=None, err=None)
def test_handled_exception_reporting_preserves_external_tools_when_unavailable():
    import sys

    import pytest

    for tool_id in (4, 3):
        sys.monitoring.use_tool_id(tool_id, f"external-{tool_id}")

    from ddtrace.errortracking._handled_exceptions.monitoring_reporting import _install_sys_monitoring_reporting
    from ddtrace.errortracking._handled_exceptions.monitoring_reporting import _uninstall_sys_monitoring_reporting
    from ddtrace.internal import monitoring

    with pytest.raises(monitoring.MonitoringToolUnavailable):
        _install_sys_monitoring_reporting()
    assert monitoring._global_exception_handler is None

    _uninstall_sys_monitoring_reporting()
    assert sys.monitoring.get_tool(4) == "external-4"
    assert sys.monitoring.get_tool(3) == "external-3"


@pytest.mark.subprocess(out=None, err=None)
def test_reporting_callback_contains_failures():
    from unittest.mock import patch

    from ddtrace.errortracking._handled_exceptions import monitoring_reporting as reporting

    with (
        patch.object(reporting.tracer, "current_span", side_effect=RuntimeError("reporting failed")),
        patch.object(reporting.log, "warning") as warning,
    ):
        reporting._handler.on_exception_handled((lambda: None).__code__, 0, ValueError("application error"))

    warning.assert_called_once_with("monitoring EXCEPTION_HANDLED handler failed", exc_info=True)
