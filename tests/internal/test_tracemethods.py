import pytest

from ddtrace.internal.tracemethods import _install_trace_methods


def test_install_trace_methods_empty():
    raw_dd_trace_methods = ""

    assert _install_trace_methods(raw_dd_trace_methods) is None


def test_install_trace_methods_error():
    raw_dd_trace_methods = "not_real_module.not_real_class[not_real_method]"

    with pytest.raises(ImportError):
        _install_trace_methods(raw_dd_trace_methods)


def test_install_trace_methods_success():
    raw_dd_trace_methods = "fastapi.testclient[stream]"

    assert _install_trace_methods(raw_dd_trace_methods) is None
