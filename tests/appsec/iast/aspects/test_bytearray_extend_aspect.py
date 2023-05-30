# -*- encoding: utf-8 -*-
import pytest

try:
    from tests.appsec.iast.aspects.conftest import _iast_patched_module
    from ddtrace.appsec.iast._taint_tracking import get_tainted_ranges
    from ddtrace.appsec.iast._taint_tracking import taint_pyobject
    from ddtrace.appsec.iast._taint_tracking import Source
    from ddtrace.appsec.iast._taint_tracking import OriginType
except (ImportError, AttributeError):
    pytest.skip("IAST not supported for this Python version", allow_module_level=True)

mod = _iast_patched_module("tests.appsec.iast.fixtures.aspects.str_methods")


class TestByteArrayExtendAspect(object):
    def test_simple_extend_not_tainted(self):
        ba1 = bytearray(b"123")
        assert not get_tainted_ranges(ba1)
        ba2 = bytearray(b"456")
        assert not get_tainted_ranges(ba2)
        result = mod.do_bytearray_extend(ba1, ba2)
        assert result == bytearray(b"123456")
        assert not get_tainted_ranges(result)

    def test_extend_with_bytes_not_tainted(self):
        ba1 = bytearray(b"123")
        b2 = b"456"
        result = mod.do_bytearray_extend(ba1, b2)
        assert result == bytearray(b"123456")
        assert not get_tainted_ranges(result)
