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
def test_handled_exception_reporting_preserves_external_tools_when_unavailable():
    import sys

    for tool_id in (4, 3):
        sys.monitoring.use_tool_id(tool_id, f"external-{tool_id}")

    from ddtrace.errortracking._handled_exceptions.monitoring_reporting import _install_sys_monitoring_reporting
    from ddtrace.errortracking._handled_exceptions.monitoring_reporting import _uninstall_sys_monitoring_reporting
    from ddtrace.internal import monitoring

    try:
        _install_sys_monitoring_reporting()
    except monitoring.MonitoringToolUnavailable:
        pass
    else:
        raise AssertionError("error tracking unexpectedly acquired an occupied tool slot")

    _uninstall_sys_monitoring_reporting()
    assert sys.monitoring.get_tool(4) == "external-4"
    assert sys.monitoring.get_tool(3) == "external-3"
