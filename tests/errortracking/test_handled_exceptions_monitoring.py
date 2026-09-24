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
def test_module_only_filter_avoids_path_resolution_and_stale_negative_cache():
    from unittest.mock import patch

    from ddtrace.errortracking._handled_exceptions import monitoring_reporting as reporting

    file_name = "/tmp/configured_module.py"
    reporting.INSTRUMENTED_FILE_PATHS.clear()
    reporting._report_configured_modules = True
    reporting._should_report_exception = None
    reporting._cached_should_report_exception.cache_clear()

    with patch.object(reporting.Path, "resolve", side_effect=AssertionError("unexpected path resolution")):
        assert not reporting.cached_should_report_exception(file_name)
        reporting.INSTRUMENTED_FILE_PATHS.add(file_name)
        assert reporting.cached_should_report_exception(file_name)


@pytest.mark.subprocess(out=None, err=None)
def test_filter_builder_preserves_configured_module_union():
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import patch

    from ddtrace.errortracking._handled_exceptions import monitoring_reporting as reporting

    configured_file = "/tmp/configured_module.py"
    unconfigured_file = "/tmp/unconfigured_module.py"
    configured_path = Path(configured_file)
    unconfigured_path = Path(unconfigured_file)
    reporting.INSTRUMENTED_FILE_PATHS.clear()
    reporting.INSTRUMENTED_FILE_PATHS.add(configured_file)

    modules_only = reporting.create_should_report_exception_optimized({"modules"})
    assert modules_only(configured_file, configured_path)
    assert not modules_only(unconfigured_file, unconfigured_path)

    with patch.object(reporting, "is_user_code", return_value=False) as is_user_code:
        modules_and_user = reporting.create_should_report_exception_optimized({"modules", "all_user"})
        assert modules_and_user(configured_file, configured_path)
        assert not modules_and_user(unconfigured_file, unconfigured_path)
        is_user_code.return_value = True
        assert modules_and_user(unconfigured_file, unconfigured_path)

    with (
        patch.object(reporting, "is_third_party", return_value=False) as is_third_party,
        patch.object(reporting, "filename_to_package", return_value=SimpleNamespace(name="package")),
    ):
        modules_and_third_party = reporting.create_should_report_exception_optimized({"modules", "all_third_party"})
        assert modules_and_third_party(configured_file, configured_path)
        assert not modules_and_third_party(unconfigured_file, unconfigured_path)
        is_third_party.return_value = True
        assert modules_and_third_party(unconfigured_file, unconfigured_path)


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
def test_reporting_uses_direct_callback():
    import sys

    from ddtrace.errortracking._handled_exceptions import monitoring_reporting as reporting
    from ddtrace.internal import monitoring

    reporting._install_sys_monitoring_reporting()
    try:
        tool_id = monitoring.get_tool_id()
        callback = reporting._handler.on_exception_handled
        assert sys.monitoring.register_callback(tool_id, sys.monitoring.events.EXCEPTION_HANDLED, callback) == callback
    finally:
        reporting._uninstall_sys_monitoring_reporting()


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
