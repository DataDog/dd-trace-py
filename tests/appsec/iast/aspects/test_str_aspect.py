# -*- coding: utf-8 -*-
import mock
import pytest

from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import as_formatted_evidence
from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects
from tests.appsec.iast.aspects.aspect_utils import BaseReplacement
from tests.appsec.iast.aspects.aspect_utils import create_taint_range_with_format
from tests.appsec.iast.aspects.conftest import _iast_patched_module


mod = _iast_patched_module("benchmarks.bm.iast_fixtures.str_methods")


def setup():
    oce._enabled = True


@pytest.mark.parametrize(
    "obj, args, kwargs",
    [
        (3.5, (), {}),
        ("Hi", (), {}),
        ("🙀", (), {}),
        (b"Hi", (), {}),
        (b"Hi", (), {"encoding": "utf-8", "errors": "strict"}),
        (b"Hi", (), {"encoding": "utf-8", "errors": "ignore"}),
        ({"a": "b", "c": "d"}, (), {}),
        ({"a", "b", "c", "d"}, (), {}),
        (("a", "b", "c", "d"), (), {}),
        (["a", "b", "c", "d"], (), {}),
    ],
)
def test_str_aspect(obj, args, kwargs):
    import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects

    obj = taint_pyobject(
        obj, source_name="test_str_aspect_tainting", source_value=obj, source_origin=OriginType.PARAMETER
    )
    assert ddtrace_aspects.str_aspect(str, 0, obj, *args, **kwargs) == str(obj, *args, **kwargs)


@pytest.mark.parametrize("obj", [dict(), set(), list(), tuple(), 3.5, "Hi", "🙀"])
def test_str_aspect_objs(obj):
    import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects

    obj = taint_pyobject(
        obj, source_name="test_str_aspect_tainting", source_value=obj, source_origin=OriginType.PARAMETER
    )
    assert ddtrace_aspects.str_aspect(str, 0, obj) == str(obj)


@pytest.mark.parametrize("obj", [dict(), set(), list(), tuple(), 3.5, "Hi", "🙀"])
@pytest.mark.parametrize(
    "args",
    [
        ("utf-8", "strict"),
        ("latin1", "strict"),
        ("iso-8859-8", "strict"),
        ("sjis", "strict"),
        ("utf-8", "replace"),
    ],
)
@pytest.mark.parametrize("kwargs", [{}, {"errors": "ignore"}, {"errors": "replace"}])
def test_str_aspect_objs_typeerror(obj, args, kwargs):
    import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects

    obj = taint_pyobject(
        obj, source_name="test_str_aspect_tainting", source_value=obj, source_origin=OriginType.PARAMETER
    )
    with pytest.raises(TypeError) as e:
        ddtrace_aspects.str_aspect(str, 0, obj, *args, **kwargs)

    with pytest.raises(TypeError) as f:
        str(obj, *args, **kwargs)

    assert str(e.value) == str(f.value)


@pytest.mark.parametrize("obj", [b"Hi", bytearray(b"Hi")])
@pytest.mark.parametrize(
    "args",
    [
        ("utf-8", "strict"),
        ("latin1", "strict"),
        ("iso-8859-8", "strict"),
        ("sjis", "strict"),
        ("utf-8", "replace"),
        ("latin1", "replace"),
        ("iso-8859-8", "replace"),
        ("sjis", "replace"),
        ("utf-8", "ignore"),
        ("latin1", "ignore"),
        ("iso-8859-8", "ignore"),
        ("sjis", "ignore"),
    ],
)
def test_str_aspect_args(obj, args):
    import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects

    obj = taint_pyobject(
        obj, source_name="test_str_aspect_tainting", source_value=obj, source_origin=OriginType.PARAMETER
    )
    assert ddtrace_aspects.str_aspect(str, 0, obj, *args) == str(obj, *args)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_str_utf(encoding):
    import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects

    obj = b"\xe8\xa8\x98\xe8\x80\x85 \xe9\x84\xad\xe5\x95\x9f\xe6\xba\x90 \xe7\xbe\x85\xe6\x99\xba\xe5\xa0\x85"

    obj = taint_pyobject(obj, source_name="test_str_utf", source_value=obj, source_origin=OriginType.PARAMETER)
    result = ddtrace_aspects.str_aspect(str, 0, obj, **{"encoding": encoding, "errors": "strict"})
    orig_result = str(obj, **{"encoding": encoding, "errors": "strict"})

    assert result == orig_result

    formatted_result = ":+-<test_str_utf>" + orig_result + "<test_str_utf>-+:"
    assert as_formatted_evidence(result) == formatted_result


def test_repr_utf16():
    import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects

    with mock.patch("ddtrace.appsec._iast._taint_tracking.aspects.iast_taint_log_error") as _iast_error_metric:
        obj = b"\xe8\xa8\x98\xe8\x80\x85 \xe9\x84\xad\xe5\x95\x9f\xe6\xba\x90 \xe7\xbe\x85\xe6\x99\xba\xe5\xa0\x85"

        obj = taint_pyobject(
            obj, source_name="test_repr_utf16", source_value=str(obj), source_origin=OriginType.PARAMETER
        )
        result = ddtrace_aspects.repr_aspect(obj.__repr__, 0, obj)

        assert result == repr(obj)
        assert is_pyobject_tainted(result)

        expected_result = "b':+-<test_repr_utf16>\\xe8\\xa8\\x98\\xe8\\x80\\x85 \\xe9\\x84\\xad\\xe5\\x95\\x9f\\xe6\\xba\\x90 \\xe7\\xbe\\x85\\xe6\\x99\\xba\\xe5\\xa0\\x85<test_repr_utf16>-+:'"  # noqa:E501
        assert as_formatted_evidence(result) == expected_result

    _iast_error_metric.assert_not_called()


def test_repr_utf16_2():
    import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects

    with mock.patch("ddtrace.appsec._iast._taint_tracking.aspects.iast_taint_log_error") as _iast_error_metric:
        obj = (
            "\xe8\xa8\x98\xe8\x80\x85 \xe9\x84\xad\xe5\x95\x9f\xe6\xba\x90 \xe7\xbe\x85\xe6\x99\xba\xe5\xa0\x85".encode(
                "utf-16"
            )
        )

        obj = taint_pyobject(
            obj, source_name="test_repr_utf16_2", source_value=str(obj), source_origin=OriginType.PARAMETER
        )
        result = ddtrace_aspects.repr_aspect(obj.__repr__, 0, obj)

        assert result == repr(obj)
        assert is_pyobject_tainted(result)

        expected_result = "b':+-<test_repr_utf16_2>" + ascii(obj)[2:-1] + "<test_repr_utf16_2>-+:'"
        assert as_formatted_evidence(result) == expected_result

    _iast_error_metric.assert_not_called()


def test_repr_nonascii():
    import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects

    with mock.patch("ddtrace.appsec._iast._taint_tracking.aspects.iast_taint_log_error") as _iast_error_metric:
        obj = "記者 鄭啟源 羅智堅"

        obj = taint_pyobject(
            obj, source_name="test_repr_nonascii", source_value=str(obj), source_origin=OriginType.PARAMETER
        )
        result = ddtrace_aspects.repr_aspect(obj.__repr__, 0, obj)

        assert result == repr(obj)
        assert is_pyobject_tainted(result)

        expected_result = "':+-<test_repr_nonascii>" + obj + "<test_repr_nonascii>-+:'"
        assert as_formatted_evidence(result) == expected_result

    _iast_error_metric.assert_not_called()


def test_repr_bytearray():
    import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects

    with mock.patch("ddtrace.appsec._iast._taint_tracking.aspects.iast_taint_log_error") as _iast_error_metric:
        obj = bytearray(
            b"\xe8\xa8\x98\xe8\x80\x85 \xe9\x84\xad\xe5\x95\x9f\xe6\xba\x90 \xe7\xbe\x85\xe6\x99\xba\xe5\xa0\x85"
        )

        obj = taint_pyobject(
            obj, source_name="test_repr_bytearray", source_value=str(obj), source_origin=OriginType.PARAMETER
        )
        result = ddtrace_aspects.repr_aspect(obj.__repr__, 0, obj)

        assert result == repr(obj)
        assert is_pyobject_tainted(result)

        expected_result = "bytearray(b':+-<test_repr_bytearray>\\xe8\\xa8\\x98\\xe8\\x80\\x85 \\xe9\\x84\\xad\\xe5\\x95\\x9f\\xe6\\xba\\x90 \\xe7\\xbe\\x85\\xe6\\x99\\xba\\xe5\\xa0\\x85<test_repr_bytearray>-+:')"  # noqa:E501
        assert as_formatted_evidence(result) == expected_result

    _iast_error_metric.assert_not_called()


@pytest.mark.parametrize(
    "obj, expected_result",
    [
        (b"Hi", ":+-<test_str_aspect_tainting>Hi<test_str_aspect_tainting>-+:"),
        (
            b"\xc3\xa9\xc3\xa7\xc3\xa0\xc3\xb1\xc3\x94\xc3\x8b",
            ":+-<test_str_aspect_tainting>éçàñÔË<test_str_aspect_tainting>-+:",
        ),
        (bytearray(b"Hi"), ":+-<test_str_aspect_tainting>Hi<test_str_aspect_tainting>-+:"),
    ],
)
@pytest.mark.parametrize(
    "encoding",
    [
        "utf-8",
    ],
)
@pytest.mark.parametrize("errors", ["ignore", "strict", "replace"])
def test_str_aspect_kwargs(obj, expected_result, encoding, errors):
    import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects

    obj = taint_pyobject(
        obj, source_name="test_str_aspect_tainting", source_value=obj, source_origin=OriginType.PARAMETER
    )
    result = ddtrace_aspects.str_aspect(str, 0, obj, **{"encoding": encoding, "errors": errors})

    assert result == str(obj, **{"encoding": encoding, "errors": errors})

    assert as_formatted_evidence(result) == expected_result


@pytest.mark.parametrize(
    "obj",
    [
        b"\xe9\xe7\xe0\xf1\xd4\xcb",
        b"\x83v\x83\x8d\x83_\x83N\x83g\x83e\x83X\x83g",
    ],
)
def test_str_aspect_decode_error(obj):
    import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects

    source_name = str(obj, "utf-8", errors="ignore")
    obj = taint_pyobject(
        obj,
        source_name="test_str_aspect_tainting",
        source_value=source_name if source_name else "invalid",
        source_origin=OriginType.PARAMETER,
    )
    with pytest.raises(UnicodeDecodeError) as e:
        ddtrace_aspects.str_aspect(str, 0, obj, **{"encoding": "utf-8", "errors": "strict"})

    with pytest.raises(UnicodeDecodeError) as f:
        str(obj, **{"encoding": "utf-8", "errors": "strict"})

    assert str(e.value) == str(f.value)


@pytest.mark.parametrize("obj", [b"Hi", bytearray(b"Hi")])
@pytest.mark.parametrize(
    "args",
    [
        (),
        ("utf-8",),
        ("latin1",),
        ("iso-8859-8",),
        ("sjis",),
    ],
)
@pytest.mark.parametrize("kwargs", [{}, {"errors": "ignore"}, {"errors": "strict"}, {"errors": "replace"}])
def test_str_aspect_encoding_kwargs(obj, args, kwargs):
    import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects

    obj = taint_pyobject(
        obj, source_name="test_str_aspect_tainting", source_value=obj, source_origin=OriginType.PARAMETER
    )
    assert ddtrace_aspects.str_aspect(str, 0, obj, *args, **kwargs) == str(obj, *args, **kwargs)


@pytest.mark.parametrize("obj", [b"Hi", bytearray(b"Hi")])
@pytest.mark.parametrize(
    "args",
    [
        ("utf-8", "strict"),
        ("latin1", "strict"),
        ("latin1", "ignore"),
        ("iso-8859-8", "ignore"),
        ("sjis", "ignore"),
    ],
)
@pytest.mark.parametrize("kwargs", [{"encoding": "latin1"}, {"errors": "strict"}, {"errors": "replace"}])
def test_str_aspect_typeerror(obj, args, kwargs):
    import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects

    obj = taint_pyobject(
        obj, source_name="test_str_aspect_tainting", source_value=obj, source_origin=OriginType.PARAMETER
    )
    with pytest.raises(TypeError) as e:
        ddtrace_aspects.str_aspect(str, 0, obj, *args, **kwargs)

    with pytest.raises(TypeError) as f:
        str(obj, *args, **kwargs)

    assert str(e.value) == str(f.value)


@pytest.mark.parametrize(
    "obj, kwargs, should_be_tainted",
    [
        (3.5, {}, False),
        ("Hi", {}, True),
        ("🙀", {}, True),
        (b"Hi", {}, True),
        (bytearray(b"Hi"), {}, True),
        (b"Hi", {"encoding": "utf-8", "errors": "strict"}, True),
        (b"Hi", {"encoding": "utf-8", "errors": "ignore"}, True),
        ({"a": "b", "c": "d"}, {}, False),
        ({"a", "b", "c", "d"}, {}, False),
        (("a", "b", "c", "d"), {}, False),
        (["a", "b", "c", "d"], {}, False),
    ],
)
def test_str_aspect_tainting(obj, kwargs, should_be_tainted):
    if should_be_tainted:
        obj = taint_pyobject(
            obj, source_name="test_str_aspect_tainting", source_value=obj, source_origin=OriginType.PARAMETER
        )

    result = ddtrace_aspects.str_aspect(str, 0, obj, **kwargs)
    assert is_pyobject_tainted(result) == should_be_tainted

    assert result == str(obj, **kwargs)


@pytest.mark.parametrize(
    "obj, expected_result, formatted_result",
    [
        ("3.5", "'3.5'", "':+-<test_repr_aspect_tainting>3.5<test_repr_aspect_tainting>-+:'"),
        ("Hi", "'Hi'", "':+-<test_repr_aspect_tainting>Hi<test_repr_aspect_tainting>-+:'"),
        ("🙀", "'🙀'", "':+-<test_repr_aspect_tainting>🙀<test_repr_aspect_tainting>-+:'"),
        (b"Hi", "b'Hi'", "b':+-<test_repr_aspect_tainting>Hi<test_repr_aspect_tainting>-+:'"),
        (
            bytearray(b"Hi"),
            "bytearray(b'Hi')",
            "bytearray(b':+-<test_repr_aspect_tainting>Hi<test_repr_aspect_tainting>-+:')",
        ),
    ],
)
def test_repr_aspect_tainting(obj, expected_result, formatted_result):
    with mock.patch("ddtrace.appsec._iast._taint_tracking.aspects.iast_taint_log_error") as _iast_error_metric:
        assert repr(obj) == expected_result

        obj = taint_pyobject(
            obj, source_name="test_repr_aspect_tainting", source_value=obj, source_origin=OriginType.PARAMETER
        )

        result = ddtrace_aspects.repr_aspect(obj.__repr__, 0, obj)
        assert is_pyobject_tainted(result) is True
        assert as_formatted_evidence(result) == formatted_result
    _iast_error_metric.assert_not_called()


def test_split_tainted_noargs():
    result = mod.do_split_no_args("abc def ghi")
    assert result == ["abc", "def", "ghi"]
    for substr in result:
        assert not get_tainted_ranges(substr)

    s_tainted = taint_pyobject(
        pyobject="abc def ghi",
        source_name="test_split_tainted",
        source_value="abc def ghi",
        source_origin=OriginType.PARAMETER,
    )
    assert get_tainted_ranges(s_tainted)

    result2 = mod.do_split_no_args(s_tainted)
    assert result2 == ["abc", "def", "ghi"]
    for substr in result2:
        assert get_tainted_ranges(substr) == [
            TaintRange(0, 3, Source("test_split_tainted", "abc", OriginType.PARAMETER)),
        ]


@pytest.mark.parametrize(
    "s, call, _args, should_be_tainted, result_list, result_tainted_list",
    [
        ("abc def", mod.do_split_no_args, [], True, ["abc", "def"], [(0, 3), (0, 3)]),
        (b"abc def", mod.do_split_no_args, [], True, [b"abc", b"def"], [(0, 3), (0, 3)]),
        (
            bytearray(b"abc def"),
            mod.do_split_no_args,
            [],
            True,
            [bytearray(b"abc"), bytearray(b"def")],
            [(0, 3), (0, 3)],
        ),
        ("abc def", mod.do_rsplit_no_args, [], True, ["abc", "def"], [(0, 3), (0, 3)]),
        (b"abc def", mod.do_rsplit_no_args, [], True, [b"abc", b"def"], [(0, 3), (0, 3)]),
        (
            bytearray(b"abc def"),
            mod.do_rsplit_no_args,
            [],
            True,
            [bytearray(b"abc"), bytearray(b"def")],
            [(0, 3), (0, 3)],
        ),
        ("abc def", mod.do_split_no_args, [], False, ["abc", "def"], []),
        ("abc def", mod.do_rsplit_no_args, [], False, ["abc", "def"], []),
        (b"abc def", mod.do_rsplit_no_args, [], False, [b"abc", b"def"], []),
        ("abc def hij", mod.do_split_no_args, [], True, ["abc", "def", "hij"], [(0, 3), (0, 3), (0, 3)]),
        (b"abc def hij", mod.do_split_no_args, [], True, [b"abc", b"def", b"hij"], [(0, 3), (0, 3), (0, 3)]),
        ("abc def hij", mod.do_rsplit_no_args, [], True, ["abc", "def", "hij"], [(0, 3), (0, 3), (0, 3)]),
        (b"abc def hij", mod.do_rsplit_no_args, [], True, [b"abc", b"def", b"hij"], [(0, 3), (0, 3), (0, 3)]),
        ("abc def hij", mod.do_split_no_args, [], False, ["abc", "def", "hij"], []),
        (b"abc def hij", mod.do_split_no_args, [], False, [b"abc", b"def", b"hij"], []),
        ("abc def hij", mod.do_rsplit_no_args, [], False, ["abc", "def", "hij"], []),
        (b"abc def hij", mod.do_rsplit_no_args, [], False, [b"abc", b"def", b"hij"], []),
        (
            bytearray(b"abc def hij"),
            mod.do_rsplit_no_args,
            [],
            False,
            [bytearray(b"abc"), bytearray(b"def"), bytearray(b"hij")],
            [],
        ),
        ("abc def hij", mod.do_split_maxsplit, [1], True, ["abc", "def hij"], [(0, 3), (0, 7)]),
        ("abc def hij", mod.do_rsplit_maxsplit, [1], True, ["abc def", "hij"], [(0, 7), (0, 3)]),
        ("abc def hij", mod.do_split_maxsplit, [1], False, ["abc", "def hij"], []),
        ("abc def hij", mod.do_rsplit_maxsplit, [1], False, ["abc def", "hij"], []),
        ("abc def hij", mod.do_split_maxsplit, [2], True, ["abc", "def", "hij"], [(0, 3), (0, 3), (0, 3)]),
        ("abc def hij", mod.do_rsplit_maxsplit, [2], True, ["abc", "def", "hij"], [(0, 3), (0, 3), (0, 3)]),
        ("abc def hij", mod.do_split_maxsplit, [2], False, ["abc", "def", "hij"], []),
        ("abc def hij", mod.do_rsplit_maxsplit, [2], False, ["abc", "def", "hij"], []),
        ("abc|def|hij", mod.do_split_separator, ["|"], True, ["abc", "def", "hij"], [(0, 3), (0, 3), (0, 3)]),
        ("abc|def|hij", mod.do_rsplit_separator, ["|"], True, ["abc", "def", "hij"], [(0, 3), (0, 3), (0, 3)]),
        ("abc|def|hij", mod.do_split_separator, ["|"], False, ["abc", "def", "hij"], []),
        ("abc|def|hij", mod.do_rsplit_separator, ["|"], False, ["abc", "def", "hij"], []),
        ("abc|def hij", mod.do_split_separator, ["|"], True, ["abc", "def hij"], [(0, 3), (0, 7)]),
        ("abc|def hij", mod.do_rsplit_separator, ["|"], True, ["abc", "def hij"], [(0, 3), (0, 7)]),
        ("abc|def hij", mod.do_split_separator, ["|"], False, ["abc", "def hij"], []),
        ("abc|def hij", mod.do_rsplit_separator, ["|"], False, ["abc", "def hij"], []),
        ("abc|def|hij", mod.do_split_separator_and_maxsplit, ["|", 1], True, ["abc", "def|hij"], [(0, 3), (0, 7)]),
        ("abc|def|hij", mod.do_rsplit_separator_and_maxsplit, ["|", 1], True, ["abc|def", "hij"], [(0, 7), (0, 3)]),
        ("abc|def|hij", mod.do_split_separator_and_maxsplit, ["|", 1], False, ["abc", "def|hij"], []),
        ("abc|def|hij", mod.do_rsplit_separator_and_maxsplit, ["|", 1], False, ["abc|def", "hij"], []),
        ("abc\ndef\nhij", mod.do_splitlines_no_arg, [], True, ["abc", "def", "hij"], [(0, 3), (0, 3), (0, 3)]),
        (b"abc\ndef\nhij", mod.do_splitlines_no_arg, [], True, [b"abc", b"def", b"hij"], [(0, 3), (0, 3), (0, 3)]),
        (
            bytearray(b"abc\ndef\nhij"),
            mod.do_splitlines_no_arg,
            [],
            True,
            [bytearray(b"abc"), bytearray(b"def"), bytearray(b"hij")],
            [(0, 3), (0, 3), (0, 3)],
        ),
        (
            "abc\ndef\nhij\n",
            mod.do_splitlines_keepends,
            [True],
            True,
            ["abc\n", "def\n", "hij\n"],
            [(0, 4), (0, 4), (0, 4)],
        ),
        (
            b"abc\ndef\nhij\n",
            mod.do_splitlines_keepends,
            [True],
            True,
            [b"abc\n", b"def\n", b"hij\n"],
            [(0, 4), (0, 4), (0, 4)],
        ),
        (
            bytearray(b"abc\ndef\nhij\n"),
            mod.do_splitlines_keepends,
            [True],
            True,
            [bytearray(b"abc\n"), bytearray(b"def\n"), bytearray(b"hij\n")],
            [(0, 4), (0, 4), (0, 4)],
        ),
    ],
)
def test_split_aspect_tainting(s, call, _args, should_be_tainted, result_list, result_tainted_list):
    _test_name = "test_split_aspect_tainting"
    if should_be_tainted:
        obj = taint_pyobject(
            s, source_name="test_split_aspect_tainting", source_value=s, source_origin=OriginType.PARAMETER
        )
    else:
        obj = s

    result = call(obj, *_args)
    assert result == result_list
    for idx, result_range in enumerate(result_tainted_list):
        result_item = result[idx]
        assert is_pyobject_tainted(result_item) == should_be_tainted
        if should_be_tainted:
            _range = get_tainted_ranges(result_item)[0]
            assert _range == TaintRange(result_range[0], result_range[1], Source(_test_name, obj, OriginType.PARAMETER))


class TestOperatorsReplacement(BaseReplacement):
    def test_aspect_ljust_str_tainted(self):
        # type: () -> None
        string_input = "foo"

        # Not tainted
        ljusted = mod.do_ljust(string_input, 4)  # pylint: disable=no-member
        assert as_formatted_evidence(ljusted) == ljusted

        # Tainted
        string_input = create_taint_range_with_format(":+-foo-+:")
        ljusted = mod.do_ljust(string_input, 4)  # pylint: disable=no-member
        assert as_formatted_evidence(ljusted) == ":+-foo-+: "

    def test_aspect_ljust_error_with_tainted_gives_one_log_metric(self, telemetry_writer):
        string_input = create_taint_range_with_format(":+-foo-+:")
        with pytest.raises(TypeError):
            mod.do_ljust(string_input, "aaaaa")

        list_metrics_logs = list(telemetry_writer._logs)
        assert len(list_metrics_logs) == 0

    def test_zfill(self):
        # Not tainted
        string_input = "-1234"
        res = mod.do_zfill(string_input, 6)  # pylint: disable=no-member
        assert as_formatted_evidence(res) == "-01234"

        # Tainted
        string_input = create_taint_range_with_format(":+--12-+:34")
        res = mod.do_zfill(string_input, 6)  # pylint: disable=no-member
        assert as_formatted_evidence(res) == ":+---+:0:+-12-+:34"

        string_input = create_taint_range_with_format(":+-+12-+:34")
        res = mod.do_zfill(string_input, 7)  # pylint: disable=no-member
        assert as_formatted_evidence(res) == ":+-+-+:00:+-12-+:34"

        string_input = create_taint_range_with_format(":+-012-+:34")
        res = mod.do_zfill(string_input, 7)  # pylint: disable=no-member
        assert as_formatted_evidence(res) == "00:+-012-+:34"

    def test_aspect_zfill_error_and_no_log_metric(self, telemetry_writer):
        string_input = create_taint_range_with_format(":+-foo-+:")
        with pytest.raises(TypeError):
            mod.do_zfill(string_input, "aaaaa")

        list_metrics_logs = list(telemetry_writer._logs)
        assert len(list_metrics_logs) == 0

    def test_format(self):
        # type: () -> None
        string_input = "foo"
        result = mod.do_format_fill(string_input)
        assert result == "foo       "
        string_input = create_taint_range_with_format(":+-foo-+:")

        result = mod.do_format_fill(string_input)  # pylint: disable=no-member
        # TODO format with params doesn't work correctly the assert should be
        #  assert as_formatted_evidence(result) == ":+-foo       -+:"
        assert as_formatted_evidence(result) == ":+-foo-+:"
