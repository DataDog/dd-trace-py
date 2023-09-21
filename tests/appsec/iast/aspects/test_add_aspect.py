#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
import sys

import pytest


try:
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import Source
    from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
    from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
    from ddtrace.appsec._iast._taint_tracking import taint_pyobject
    from ddtrace.appsec._iast._taint_tracking import taint_ranges_as_evidence_info
    from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import TaintRange_
    import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects
    from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect
    from ddtrace.appsec._iast._utils import _is_python_version_supported as python_supported_by_iast
except (ImportError, AttributeError):
    pytest.skip("IAST not supported for this Python version", allow_module_level=True)


@pytest.mark.parametrize(
    "obj1, obj2",
    [
        (3.5, 3.3),
        # (complex(2, 1), complex(3, 4)),
        ("Hello ", "world"),
        ("🙀", "🌝"),
        (b"Hi", b""),
        (["a"], ["b"]),
        (bytearray("a", "utf-8"), bytearray("b", "utf-8")),
        (("a", "b"), ("c", "d")),
    ],
)
@pytest.mark.skipif(sys.version_info < (3, 6, 0), reason="Python 3.6+ only")
def test_add_aspect_successful(obj1, obj2):
    assert ddtrace_aspects.add_aspect(obj1, obj2) == obj1 + obj2


@pytest.mark.parametrize(
    "obj1, obj2",
    [(b"Hi", ""), ("Hi", b""), ({"a", "b"}, {"c", "d"}), (dict(), dict())],
)
@pytest.mark.skipif(sys.version_info < (3, 6, 0), reason="Python 3.6+ only")
def test_add_aspect_type_error(obj1, obj2):
    with pytest.raises(TypeError) as e_info1:
        obj1 + obj2

    with pytest.raises(TypeError) as e_info2:
        ddtrace_aspects.add_aspect(obj1, obj2)

    assert str(e_info2.value) == str(e_info1.value)


@pytest.mark.parametrize(
    "obj1, obj2, should_be_tainted",
    [
        (3.5, 3.3, False),
        (complex(2, 1), complex(3, 4), False),
        ("Hello ", "world", True),
        (b"bye ", b"".join((b"bye", b" ")), True),
        ("🙀", "".join(("🙀", "")), True),
        ("a", "a", True),
        (b"a", b"a", True),
        (b"Hi", b"", True),
        (b"Hi ", b" world", True),
        (["a"], ["b"], False),
        (bytearray(b"a"), bytearray(b"b"), True),
        (("a", "b"), ("c", "d"), False),
    ],
)
@pytest.mark.skipif(sys.version_info < (3, 6, 0), reason="Python 3.6+ only")
def test_add_aspect_tainting_left_hand(obj1, obj2, should_be_tainted):

    if should_be_tainted:
        obj1 = taint_pyobject(
            pyobject=obj1,
            source_name="test_add_aspect_tainting_left_hand",
            source_value=obj1,
            source_origin=OriginType.PARAMETER,
        )
        if len(obj1):
            assert get_tainted_ranges(obj1)

    result = ddtrace_aspects.add_aspect(obj1, obj2)
    assert result == obj1 + obj2
    if isinstance(obj2, (bytes, str, bytearray)) and len(obj2):
        assert result is not obj1 + obj2
    assert is_pyobject_tainted(result) == should_be_tainted
    if should_be_tainted:
        assert get_tainted_ranges(result) == get_tainted_ranges(obj1)


@pytest.mark.parametrize(
    "obj1, obj2, should_be_tainted",
    [
        (3.5, 3.3, False),
        (complex(2, 1), complex(3, 4), False),
        ("Hello ", "world", True),
        (b"a", b"a", True),
        (b"bye ", b"bye ", True),
        ("🙀", "🌝", True),
        (b"Hi", b"", False),
        (["a"], ["b"], False),
        (bytearray("a", "utf-8"), bytearray("b", "utf-8"), True),
        (("a", "b"), ("c", "d"), False),
    ],
)
@pytest.mark.skipif(sys.version_info < (3, 6, 0), reason="Python 3.6+ only")
def test_add_aspect_tainting_right_hand(obj1, obj2, should_be_tainted):
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
    from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
    from ddtrace.appsec._iast._taint_tracking import taint_pyobject

    if should_be_tainted:
        obj2 = taint_pyobject(
            pyobject=obj2,
            source_name="test_add_aspect_tainting_right_hand",
            source_value=obj2,
            source_origin=OriginType.PARAMETER,
        )
        if len(obj2):
            assert get_tainted_ranges(obj2)

    result = ddtrace_aspects.add_aspect(obj1, obj2)

    assert result == obj1 + obj2

    assert is_pyobject_tainted(result) == should_be_tainted
    if isinstance(obj2, (str, bytes, bytearray)) and len(obj2):
        tainted_ranges = get_tainted_ranges(result)
        assert type(tainted_ranges) is list
        assert all(type(c) is TaintRange_ for c in tainted_ranges)
        assert (tainted_ranges != []) == should_be_tainted
        if should_be_tainted:
            assert len(tainted_ranges) == len(get_tainted_ranges(obj1)) + len(get_tainted_ranges(obj2))


def test_taint_ranges_as_evidence_info_nothing_tainted():
    text = "nothing tainted"
    value_parts, sources = taint_ranges_as_evidence_info(text)
    assert value_parts == [{"value": text}]
    assert sources == []


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_taint_ranges_as_evidence_info_all_tainted():
    arg = "all tainted"
    input_info = Source("request_body", arg, OriginType.PARAMETER)
    tainted_text = taint_pyobject(arg, source_name="request_body", source_value=arg, source_origin=OriginType.PARAMETER)
    value_parts, sources = taint_ranges_as_evidence_info(tainted_text)
    assert value_parts == [{"value": tainted_text, "source": 0}]
    assert sources == [input_info]


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_taint_ranges_as_evidence_info_tainted_op1_add():
    arg = "tainted part"
    input_info = Source("request_body", arg, OriginType.PARAMETER)
    text = "|not tainted part|"
    tainted_text = taint_pyobject(arg, source_name="request_body", source_value=arg, source_origin=OriginType.PARAMETER)
    tainted_add_result = add_aspect(tainted_text, text)

    value_parts, sources = taint_ranges_as_evidence_info(tainted_add_result)
    assert value_parts == [{"value": tainted_text, "source": 0}, {"value": text}]
    assert sources == [input_info]


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_taint_ranges_as_evidence_info_tainted_op2_add():
    arg = "tainted part"
    input_info = Source("request_body", arg, OriginType.PARAMETER)
    text = "|not tainted part|"
    tainted_text = taint_pyobject(arg, source_name="request_body", source_value=arg, source_origin=OriginType.PARAMETER)
    tainted_add_result = add_aspect(text, tainted_text)

    value_parts, sources = taint_ranges_as_evidence_info(tainted_add_result)
    assert value_parts == [{"value": text}, {"value": tainted_text, "source": 0}]
    assert sources == [input_info]


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_taint_ranges_as_evidence_info_same_tainted_op1_and_op3_add():
    arg = "tainted part"
    input_info = Source("request_body", arg, OriginType.PARAMETER)
    text = "|not tainted part|"
    tainted_text = taint_pyobject(arg, source_name="request_body", source_value=arg, source_origin=OriginType.PARAMETER)
    tainted_add_result = add_aspect(tainted_text, add_aspect(text, tainted_text))

    value_parts, sources = taint_ranges_as_evidence_info(tainted_add_result)
    assert value_parts == [{"value": tainted_text, "source": 0}, {"value": text}, {"value": tainted_text, "source": 0}]
    assert sources == [input_info]


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_taint_ranges_as_evidence_info_different_tainted_op1_and_op3_add():
    arg1 = "tainted body"
    arg2 = "tainted header"
    input_info1 = Source("request_body", arg1, OriginType.PARAMETER)
    input_info2 = Source("request_body", arg2, OriginType.PARAMETER)
    text = "|not tainted part|"
    tainted_text1 = taint_pyobject(
        arg1, source_name="request_body", source_value=arg1, source_origin=OriginType.PARAMETER
    )
    tainted_text2 = taint_pyobject(
        arg2, source_name="request_body", source_value=arg2, source_origin=OriginType.PARAMETER
    )
    tainted_add_result = add_aspect(tainted_text1, add_aspect(text, tainted_text2))

    value_parts, sources = taint_ranges_as_evidence_info(tainted_add_result)
    assert value_parts == [
        {"value": tainted_text1, "source": 0},
        {"value": text},
        {"value": tainted_text2, "source": 1},
    ]
    assert sources == [input_info1, input_info2]
