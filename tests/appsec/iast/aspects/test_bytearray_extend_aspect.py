# -*- encoding: utf-8 -*-
import logging

import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import create_context
from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking import reset_context
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from tests.appsec.iast.aspects.conftest import _iast_patched_module
from tests.utils import override_env


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

    def test_extend_native_exception_no_crash(self):
        from ddtrace.appsec._iast._taint_tracking.aspects import _extend_aspect

        ba1 = bytearray(b"123")
        b2 = 456
        with pytest.raises(TypeError):
            _extend_aspect(ba1, b2)

    def test_extend_first_tainted(self):
        ba1 = taint_pyobject(
            pyobject=bytearray(b"123"), source_name="test", source_value="foo", source_origin=OriginType.PARAMETER
        )
        ba2 = bytearray(b"456")

        result = mod.do_bytearray_extend(ba1, ba2)
        assert result == bytearray(b"123456")
        assert ba1 == bytearray(b"123456")
        ranges = get_tainted_ranges(result)
        assert ranges == [TaintRange(0, 3, Source("test", "foo", OriginType.PARAMETER))]
        assert get_tainted_ranges(ba1) == [TaintRange(0, 3, Source("test", "foo", OriginType.PARAMETER))]
        assert not get_tainted_ranges(ba2)

    def test_extend_first_tainted_second_bytes(self):
        ba1 = taint_pyobject(
            pyobject=bytearray(b"123"), source_name="test", source_value="foo", source_origin=OriginType.PARAMETER
        )
        ba2 = b"456"

        result = mod.do_bytearray_extend(ba1, ba2)
        assert result == bytearray(b"123456")
        ranges = get_tainted_ranges(result)
        assert ranges == [TaintRange(0, 3, Source("test", "foo", OriginType.PARAMETER))]
        assert get_tainted_ranges(ba1) == [TaintRange(0, 3, Source("test", "foo", OriginType.PARAMETER))]
        assert not get_tainted_ranges(ba2)

    def test_extend_second_tainted(self):
        ba1 = bytearray(b"123")
        ba2 = taint_pyobject(
            pyobject=bytearray(b"456"), source_name="test", source_value="foo", source_origin=OriginType.PARAMETER
        )
        result = mod.do_bytearray_extend(ba1, ba2)
        assert result == bytearray(b"123456")
        ranges = get_tainted_ranges(result)
        assert ranges == [TaintRange(3, 3, Source("test", "foo", OriginType.PARAMETER))]
        assert get_tainted_ranges(ba1) == ranges

    def test_extend_second_tainted_bytes(self):
        ba1 = bytearray(b"123")
        ba2 = taint_pyobject(
            pyobject=bytearray(b"456"), source_name="test", source_value="foo", source_origin=OriginType.PARAMETER
        )
        result = mod.do_bytearray_extend(ba1, ba2)
        assert result == bytearray(b"123456")
        ranges = get_tainted_ranges(result)
        assert ranges == [TaintRange(3, 3, Source("test", "foo", OriginType.PARAMETER))]
        assert get_tainted_ranges(ba1) == ranges

    def test_first_and_second_tainted(self):
        ba1 = taint_pyobject(
            pyobject=bytearray(b"123"), source_name="test1", source_value="foo", source_origin=OriginType.PARAMETER
        )
        ba2 = taint_pyobject(
            pyobject=bytearray(b"456"), source_name="test2", source_value="bar", source_origin=OriginType.BODY
        )
        result = mod.do_bytearray_extend(ba1, ba2)
        assert result == bytearray(b"123456")
        ranges = get_tainted_ranges(result)
        assert len(ranges) == 2
        assert ranges == [
            TaintRange(0, 3, Source("test1", "foo", OriginType.PARAMETER)),
            TaintRange(3, 3, Source("test2", "bar", OriginType.BODY)),
        ]
        assert get_tainted_ranges(ba1) == ranges
        assert get_tainted_ranges(ba2) == [TaintRange(0, 3, Source("test2", "bar", OriginType.BODY))]


@pytest.mark.skip_iast_check_logs
def test_propagate_ranges_with_no_context(caplog):
    create_context()
    ba1 = bytearray(b"123")
    ba2 = taint_pyobject(
        pyobject=bytearray(b"456"), source_name="test", source_value="foo", source_origin=OriginType.PARAMETER
    )
    reset_context()
    with override_env({IAST.ENV_DEBUG: "true"}), caplog.at_level(logging.DEBUG):
        result = mod.do_bytearray_extend(ba1, ba2)
        assert result == bytearray(b"123456")
    log_messages = [record.message for record in caplog.get_records("call")]
    assert len(log_messages) == 0
