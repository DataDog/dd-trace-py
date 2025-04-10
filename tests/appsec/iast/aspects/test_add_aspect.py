import logging
from pathlib import Path
from pathlib import PosixPath

from hypothesis import given
from hypothesis.strategies import from_type
from hypothesis.strategies import one_of
import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._context import create_context
from ddtrace.appsec._iast._taint_tracking._context import reset_context
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import TaintRange_
from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects
from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect
from tests.appsec.iast.conftest import _end_iast_context_and_oce
from tests.appsec.iast.conftest import _start_iast_context_and_oce
from tests.appsec.iast.iast_utils import string_strategies
from tests.utils import override_env
from tests.utils import override_global_config


@pytest.mark.parametrize(
    "obj1, obj2",
    [
        (3.5, 3.3),
        (complex(2, 1), complex(3, 4)),
        ("Hello ", "world"),
        ("🙀", "🌝"),
        (b"Hi", b""),
        (["a"], ["b"]),
        (bytearray("a", "utf-8"), bytearray("b", "utf-8")),
        (("a", "b"), ("c", "d")),
    ],
)
def test_add_aspect_successful(obj1, obj2):
    assert ddtrace_aspects.add_aspect(obj1, obj2) == obj1 + obj2


@given(one_of(string_strategies))
def test_add_aspect_text_successful(text):
    assert ddtrace_aspects.add_aspect(text, text) == text + text


@given(from_type(int))
def test_add_aspect_int_successful(text):
    assert ddtrace_aspects.add_aspect(text, text) == text + text


@given(from_type(Path))
def test_add_aspect_path_error(path):
    with pytest.raises(TypeError) as exc_info:
        ddtrace_aspects.add_aspect(path, path)
    assert str(exc_info.value) == "unsupported operand type(s) for +: 'PosixPath' and 'PosixPath'"


@given(from_type(PosixPath))
def test_add_aspect_posixpath_error(posixpath):
    with pytest.raises(TypeError) as exc_info:
        ddtrace_aspects.add_aspect(posixpath, posixpath)
    assert str(exc_info.value) == "unsupported operand type(s) for +: 'PosixPath' and 'PosixPath'"


@pytest.mark.parametrize(
    "obj1, obj2",
    [(b"Hi", ""), ("Hi", b""), ({"a", "b"}, {"c", "d"}), (dict(), dict())],
)
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
def test_add_aspect_tainting_right_hand(obj1, obj2, should_be_tainted):
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


@pytest.mark.parametrize(
    "obj1",
    [
        "abc",
        b"abc",
    ],
)
def test_add_aspect_tainting_add_itself(obj1):
    obj1 = taint_pyobject(
        pyobject=obj1,
        source_name="test_add_aspect_tainting_left_hand",
        source_value=obj1,
        source_origin=OriginType.PARAMETER,
    )

    result = ddtrace_aspects.add_aspect(obj1, obj1)
    assert result == obj1 + obj1

    assert is_pyobject_tainted(result) is True
    ranges_result = get_tainted_ranges(result)
    assert len(ranges_result) == 2
    assert ranges_result[0].start == 0
    assert ranges_result[0].length == 3
    assert ranges_result[1].start == 3
    assert ranges_result[1].length == 3


@pytest.mark.parametrize(
    "obj1",
    [
        "abc",
        b"abc",
    ],
)
def test_add_aspect_tainting_add_itself_twice(obj1):
    obj1 = taint_pyobject(
        pyobject=obj1,
        source_name="test_add_aspect_tainting_left_hand",
        source_value=obj1,
        source_origin=OriginType.PARAMETER,
    )

    result = ddtrace_aspects.add_aspect(obj1, obj1)
    result = ddtrace_aspects.add_aspect(obj1, obj1)
    assert result == obj1 + obj1

    assert is_pyobject_tainted(result) is True
    ranges_result = get_tainted_ranges(result)
    assert len(ranges_result) == 2
    assert ranges_result[0].start == 0
    assert ranges_result[0].length == 3
    assert ranges_result[1].start == 3
    assert ranges_result[1].length == 3


@pytest.mark.parametrize(
    "obj1, obj2",
    [
        ("abc", "def"),
        (b"abc", b"def"),
    ],
)
def test_add_aspect_tainting_add_right_twice(obj1, obj2):
    obj1 = taint_pyobject(
        pyobject=obj1,
        source_name="test_add_aspect_tainting_left_hand",
        source_value=obj1,
        source_origin=OriginType.PARAMETER,
    )

    result = ddtrace_aspects.add_aspect(obj1, obj2)
    result = ddtrace_aspects.add_aspect(obj1, obj2)
    assert result == obj1 + obj2

    assert is_pyobject_tainted(result) is True
    ranges_result = get_tainted_ranges(result)
    assert len(ranges_result) == 1
    assert ranges_result[0].start == 0
    assert ranges_result[0].length == 3


@pytest.mark.parametrize(
    "obj1, obj2",
    [
        ("abc", "def"),
        (b"abc", b"def"),
    ],
)
def test_add_aspect_tainting_add_left_twice(obj1, obj2):
    obj1 = taint_pyobject(
        pyobject=obj1,
        source_name="test_add_aspect_tainting_left_hand",
        source_value=obj1,
        source_origin=OriginType.PARAMETER,
    )

    result = ddtrace_aspects.add_aspect(obj2, obj1)  # noqa
    result = ddtrace_aspects.add_aspect(obj2, obj1)
    assert result == obj2 + obj1

    assert is_pyobject_tainted(result) is True
    ranges_result = get_tainted_ranges(result)
    assert len(ranges_result) == 1
    assert ranges_result[0].start == 3
    assert ranges_result[0].length == 3


@pytest.mark.skip_iast_check_logs
@pytest.mark.parametrize(
    "log_level, iast_debug",
    [
        (logging.DEBUG, ""),
        (logging.WARNING, ""),
        (logging.DEBUG, "false"),
        (logging.WARNING, "false"),
        (logging.DEBUG, "true"),
        (logging.WARNING, "true"),
    ],
)
def test_taint_object_error_with_no_context(log_level, iast_debug, caplog):
    """Test taint_pyobject without context. This test is to ensure that the function does not raise an exception."""
    string_to_taint = "my_string"
    _end_iast_context_and_oce()
    _start_iast_context_and_oce()
    result = taint_pyobject(
        pyobject=string_to_taint,
        source_name="test_add_aspect_tainting_left_hand",
        source_value=string_to_taint,
        source_origin=OriginType.PARAMETER,
    )

    ranges_result = get_tainted_ranges(result)
    assert len(ranges_result) == 1

    _end_iast_context_and_oce()
    with override_global_config(dict(_iast_debug=True)), caplog.at_level(log_level):
        result = taint_pyobject(
            pyobject=string_to_taint,
            source_name="test_add_aspect_tainting_left_hand",
            source_value=string_to_taint,
            source_origin=OriginType.PARAMETER,
        )

        ranges_result = get_tainted_ranges(result)
        assert len(ranges_result) == 0

        assert not any("iast::propagation::native::error::" in record.message for record in caplog.records)

        _start_iast_context_and_oce()
        result = taint_pyobject(
            pyobject=string_to_taint,
            source_name="test_add_aspect_tainting_left_hand",
            source_value=string_to_taint,
            source_origin=OriginType.PARAMETER,
        )

        ranges_result = get_tainted_ranges(result)
        assert len(ranges_result) == 1


@pytest.mark.skip_iast_check_logs
def test_get_ranges_from_object_with_no_context():
    """Test taint_pyobject without context. This test is to ensure that the function does not raise an exception."""
    string_to_taint = "my_string"
    create_context()
    result = taint_pyobject(
        pyobject=string_to_taint,
        source_name="test_add_aspect_tainting_left_hand",
        source_value=string_to_taint,
        source_origin=OriginType.PARAMETER,
    )

    reset_context()
    ranges_result = get_tainted_ranges(result)
    assert len(ranges_result) == 0


@pytest.mark.skip_iast_check_logs
def test_propagate_ranges_with_no_context(caplog):
    """Test taint_pyobject without context. This test is to ensure that the function does not raise an exception."""
    string_to_taint = "my_string"
    create_context()
    result = taint_pyobject(
        pyobject=string_to_taint,
        source_name="test_add_aspect_tainting_left_hand",
        source_value=string_to_taint,
        source_origin=OriginType.PARAMETER,
    )

    reset_context()
    with override_env({"_DD_IAST_USE_ROOT_SPAN": "false"}), override_global_config(
        dict(_iast_debug=True)
    ), caplog.at_level(logging.DEBUG):
        result_2 = add_aspect(result, "another_string")

    create_context()
    ranges_result = get_tainted_ranges(result_2)
    log_messages = [record.message for record in caplog.get_records("call")]
    assert not any("iast::" in message for message in log_messages), log_messages
    assert len(ranges_result) == 0
