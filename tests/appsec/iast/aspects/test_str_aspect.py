# -*- coding: utf-8 -*-
import pytest

from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import as_formatted_evidence
from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects
from tests.appsec.iast.aspects.aspect_utils import BaseReplacement
from tests.appsec.iast.aspects.aspect_utils import create_taint_range_with_format
from tests.appsec.iast.aspects.conftest import _iast_patched_module


mod = _iast_patched_module("tests.appsec.iast.fixtures.aspects.str_methods")


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


def test_str_utf16():
    import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects

    obj = b"\xe8\xa8\x98\xe8\x80\x85 \xe9\x84\xad\xe5\x95\x9f\xe6\xba\x90 \xe7\xbe\x85\xe6\x99\xba\xe5\xa0\x85"

    obj = taint_pyobject(
        obj, source_name="test_str_aspect_tainting", source_value=obj, source_origin=OriginType.PARAMETER
    )
    result = ddtrace_aspects.str_aspect(str, 0, obj, **{"encoding": "utf-16", "errors": "strict"})

    assert result == str(obj, **{"encoding": "utf-16", "errors": "strict"})

    # FIXME: This looks like a bug
    # expected_result = ":+-<test_str_aspect_tainting>記者 鄭啟源 羅智堅<test_str_aspect_tainting>-+:"
    # assert as_formatted_evidence(result) == expected_result


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

    obj = taint_pyobject(
        obj,
        source_name="test_str_aspect_tainting",
        source_value=str(obj, "utf-8", errors="ignore"),
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
    "obj, expected_result",
    [
        ("3.5", "'3.5'"),
        ("Hi", "'Hi'"),
        ("🙀", "'🙀'"),
        (b"Hi", "b'Hi'"),
        (bytearray(b"Hi"), "bytearray(b'Hi')"),
    ],
)
def test_repr_aspect_tainting(obj, expected_result):
    assert repr(obj) == expected_result

    obj = taint_pyobject(
        obj, source_name="test_repr_aspect_tainting", source_value=obj, source_origin=OriginType.PARAMETER
    )

    result = ddtrace_aspects.repr_aspect(repr, 0, obj)
    assert is_pyobject_tainted(result) is True


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

    def test_aspect_ljust_error_and_no_log_metric(self, telemetry_writer):
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
        assert as_formatted_evidence(result) == "foo       "
