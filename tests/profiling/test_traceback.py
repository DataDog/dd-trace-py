from ddtrace.profiling import _traceback


def test_traceback():
    assert _traceback.format_exception(Exception("hello")) == "Exception: hello"
