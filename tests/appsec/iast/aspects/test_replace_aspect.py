#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
import sys

import pytest


try:
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
    from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
    from ddtrace.appsec._iast._taint_tracking import taint_pyobject
    import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects
except (ImportError, AttributeError):
    pytest.skip("IAST not supported for this Python version", allow_module_level=True)


@pytest.mark.parametrize(
    "obj1, obj2",
    [
        ("Hello ", "world"),
        ("🙀", "🌝"),
        (b"Hi", b""),
        (bytearray("a", "utf-8"), bytearray("b", "utf-8")),
    ],
)
@pytest.mark.skipif(sys.version_info < (3, 6, 0), reason="Python 3.6+ only")
def test_replace_aspect_successful(obj1, obj2):
    assert ddtrace_aspects.replace_aspect(obj1.replace, obj1, obj1, obj2) == obj2


@pytest.mark.parametrize(
    "obj1, obj2",
    [(b"Hi", ""), ("Hi", b"")],
)
@pytest.mark.skipif(sys.version_info < (3, 6, 0), reason="Python 3.6+ only")
def test_replace_aspect_type_error(obj1, obj2):
    with pytest.raises(TypeError) as e_info1:
        obj1.replace(obj1, obj2)

    with pytest.raises(TypeError) as e_info2:
        ddtrace_aspects.replace_aspect(obj1.replace, obj1, obj1, obj2)

    assert str(e_info2.value) == str(e_info1.value)


@pytest.mark.parametrize(
    "obj1, obj2, should_be_tainted",
    [
        ("Hello ", "world", False),
        ("Hello ", "world", True),
        (b"bye ", b"".join((b"bye", b" ")), False),
        ("🙀", "".join(("🙀", "")), False),
        ("a", "b", False),
        (b"a", b"a", False),
        (b"Hi", b"", False),
        (b"Hi ", b" world", False),
        (bytearray(b"a"), bytearray(b"b"), False),
    ],
)
@pytest.mark.skipif(sys.version_info < (3, 6, 0), reason="Python 3.6+ only")
def test_replace_aspect_tainting_left_hand(obj1, obj2, should_be_tainted):

    if should_be_tainted:
        obj1 = taint_pyobject(
            pyobject=obj1,
            source_name="test_add_aspect_tainting_left_hand",
            source_value=obj1,
            source_origin=OriginType.PARAMETER,
        )
        if len(obj1):
            assert get_tainted_ranges(obj1)

    result = ddtrace_aspects.replace_aspect(obj1.replace, obj1, obj1, obj2)
    assert result == obj2
    if isinstance(obj2, (bytes, str, bytearray)) and len(obj2):
        assert result is not obj2
    assert is_pyobject_tainted(result) == False
    if should_be_tainted:
        assert get_tainted_ranges(result) == get_tainted_ranges(obj2)


@pytest.mark.parametrize(
    "obj1, obj2, orig_should_be_tainted, result_should_be_tainted",
    [
        ("Hello ", "world", False, False),
        ("Hello ", "world", True, True),
        (b"bye ", b"".join((b"bye", b" ")), False, False),
        ("🙀", "".join(("🙀", "")), False, False),
        ("a", "b", False, False),
        (b"a", b"a", False, False),
        (b"a", b"a", True, False),
        (b"Hi", b"", False, False),
        (b"Hi ", b" world", False, False),
        (bytearray(b"a"), bytearray(b"b"), False, False),
    ],
)
@pytest.mark.skipif(sys.version_info < (3, 6, 0), reason="Python 3.6+ only")
def test_replace_aspect_tainting_left_hand(obj1, obj2, orig_should_be_tainted, result_should_be_tainted):

    if orig_should_be_tainted:
        obj1 = taint_pyobject(
            pyobject=obj1,
            source_name="test_add_aspect_tainting_left_hand",
            source_value=obj1,
            source_origin=OriginType.PARAMETER,
        )
        if len(obj1):
            assert get_tainted_ranges(obj1)

    result = ddtrace_aspects.replace_aspect(obj1.replace, obj1, obj2, obj2)
    assert result == obj1
    if isinstance(obj2, (bytes, str, bytearray)) and len(obj2):
        assert result is not obj2
    assert is_pyobject_tainted(result) == result_should_be_tainted
    if result_should_be_tainted:
        assert get_tainted_ranges(result) == get_tainted_ranges(obj1)
