import pytest
from ddtrace.appsec.iast._taint_tracking._native.aspect_helpers import common_replace


def test_common_replace_untainted():
    s = "foobar"
    assert common_replace("upper", s) == "FOOBAR"
