#!/usr/bin/env python3
import pytest

from ddtrace.appsec._common_module_patches import patch_common_modules
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import bytesio_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import stringio_aspect
from tests.utils import override_global_config


@pytest.mark.parametrize(
    "aspect, text",
    [
        (stringio_aspect, "foobar"),
        (bytesio_aspect, b"foobar"),
    ],
)
def test_stringio_aspect_read(aspect, text):
    with override_global_config(dict(_iast_enabled=True)):
        patch_common_modules()
        tainted = taint_pyobject(
            pyobject=text,
            source_name="test_stringio_read_aspect_tainted_string",
            source_value=text,
            source_origin=OriginType.PARAMETER,
        )
        sio = aspect(None, 0, tainted)
        val = sio.read()
        assert is_pyobject_tainted(val)
        ranges = get_tainted_ranges(val)
        assert len(ranges) == 1
        assert ranges[0].start == 0
        assert ranges[0].length == 6


@pytest.mark.skip("TODO: APPSEC-55319")
@pytest.mark.parametrize(
    "aspect, text, added_text",
    [
        (stringio_aspect, "foobar", "foobazbazfoo"),
        (bytesio_aspect, b"foobar", b"foobazbazfoo"),
    ],
)
def test_stringio_aspect_read_with_offset(aspect, text, added_text):
    with override_global_config(dict(_iast_enabled=True)):
        patch_common_modules()
        not_tainted = added_text
        tainted = taint_pyobject(
            pyobject=text,
            source_name="test_stringio_read_aspect_tainted_string",
            source_value=text,
            source_origin=OriginType.PARAMETER,
        )
        added = add_aspect(not_tainted, tainted)
        sio = aspect(None, 0, added)
        val = sio.read(10)
        # If the StringIO() and read() aspects were perfect, `val` would not be tainted
        assert not is_pyobject_tainted(val)
        ranges = get_tainted_ranges(val)
        assert len(ranges) == 0

        val_tainted = sio.read(5)
        assert is_pyobject_tainted(val_tainted)
        ranges = get_tainted_ranges(val_tainted)
        assert len(ranges) == 1


# Check the current behaviour of always tainting read() results from offset 0
@pytest.mark.parametrize(
    "aspect, text, added_text",
    [
        (stringio_aspect, "foobar", "foobazbazfoo"),
        (bytesio_aspect, b"foobar", b"foobazbazfoo"),
    ],
)
def test_stringio_always_tainted_from_zero(aspect, text, added_text):
    with override_global_config(dict(_iast_enabled=True)):
        patch_common_modules()
        not_tainted = added_text
        tainted = taint_pyobject(
            pyobject=text,
            source_name="test_stringio_read_aspect_tainted_string",
            source_value=text,
            source_origin=OriginType.PARAMETER,
        )
        added = add_aspect(not_tainted, tainted)
        sio = aspect(None, 0, added)
        read_len = 10
        val = sio.read(read_len)
        # If the StringIO() and read() aspects were perfect, `val` would not be tainted
        ranges = get_tainted_ranges(val)
        assert len(ranges) == 1
        assert ranges[0].start == 0
        assert ranges[0].length == read_len

        read_len = 5
        val_tainted = sio.read(read_len)
        ranges = get_tainted_ranges(val_tainted)
        assert len(ranges) == 1
        assert ranges[0].start == 0
        assert ranges[0].length == read_len
        assert is_pyobject_tainted(val_tainted)


# Check the current behaviour of always tainting read() results from offset 0
def test_bytesio_always_tainted_from_zero():
    with override_global_config(dict(_iast_enabled=True)):
        patch_common_modules()
        not_tainted = b"foobazbazfoo"
        tainted = taint_pyobject(
            pyobject=b"foobar",
            source_name="test_bytesio_read_aspect_tainted_string",
            source_value=b"foobar",
            source_origin=OriginType.PARAMETER,
        )
        added = add_aspect(not_tainted, tainted)
        sio = bytesio_aspect(None, 0, added)
        read_len = 10
        val = sio.read(read_len)
        # If the bytesio() and read() aspects were perfect, `val` would not be tainted
        ranges = get_tainted_ranges(val)
        assert len(ranges) == 1
        assert ranges[0].start == 0
        assert ranges[0].length == read_len

        read_len = 5
        val_tainted = sio.read(read_len)
        ranges = get_tainted_ranges(val_tainted)
        assert len(ranges) == 1
        assert ranges[0].start == 0
        assert ranges[0].length == read_len
        assert is_pyobject_tainted(val_tainted)
