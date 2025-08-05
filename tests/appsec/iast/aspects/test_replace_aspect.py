#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
from unittest import mock

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import as_formatted_evidence
from ddtrace.appsec._iast._taint_tracking import set_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted
import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects
from ddtrace.internal.compat import PYTHON_VERSION_INFO


def _build_sample_range(start, end, name):  # type: (int, int) -> TaintRange
    return TaintRange(start, end, Source(name, "sample_value", OriginType.PARAMETER))


def _test_replace_result(
    expected_exception,
    origstr,
    substr,
    replstr,
    maxcount,
    should_be_tainted_origstr,
    should_be_tainted_replstr,
    str_type,
):
    with mock.patch("ddtrace.appsec._iast._taint_tracking.aspects.iast_propagation_error_log") as _iast_error_metric:
        if str_type == bytes:
            origstr = str_type(origstr, encoding="utf-8")
            substr = str_type(substr, encoding="utf-8")
            replstr = str_type(replstr, encoding="utf-8")
        elif str_type == bytearray:
            origstr = str_type(bytes(origstr, encoding="utf-8"))
            substr = str_type(bytes(substr, encoding="utf-8"))
            replstr = str_type(bytes(replstr, encoding="utf-8"))

        if should_be_tainted_origstr:
            origstr = taint_pyobject(
                pyobject=origstr,
                source_name="test_replace_tainted_orig",
                source_value=origstr,
                source_origin=OriginType.PARAMETER,
            )

        if should_be_tainted_replstr:
            replstr = taint_pyobject(
                pyobject=replstr,
                source_name="test_replace_tainted_orig",
                source_value=replstr,
                source_origin=OriginType.PARAMETER,
            )
        if expected_exception is not None:
            with pytest.raises(expected_exception):
                if maxcount is None:
                    replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr)
                    assert replaced == origstr.replace(substr, replstr)
                else:
                    replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr, maxcount)
                    assert replaced == origstr.replace(substr, replstr, maxcount)

                if (
                    (should_be_tainted_origstr or (replaced != origstr))
                    and should_be_tainted_replstr
                    and replaced
                    and replstr
                ):
                    assert is_pyobject_tainted(replaced)

                if not should_be_tainted_origstr and not should_be_tainted_replstr:
                    assert not is_pyobject_tainted(replaced)
        else:
            if maxcount is None:
                replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr)
                assert replaced == origstr.replace(substr, replstr)
            else:
                replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr, maxcount)
                assert replaced == origstr.replace(substr, replstr, maxcount)

            if (
                (should_be_tainted_origstr or (replaced != origstr))
                and should_be_tainted_replstr
                and replaced
                and replstr
            ):
                assert is_pyobject_tainted(replaced)

            if not should_be_tainted_origstr and not should_be_tainted_replstr:
                assert not is_pyobject_tainted(replaced)

    _iast_error_metric.assert_not_called()


@pytest.mark.parametrize(
    "origstr, substr, replstr, maxcount",
    [
        ("abbbc", "bbb", "d", None),
        ("cbc", "b", "de", -1),
        ("dbc", "b", "de", 0),
        ("ebbbcbbb", "bbb", "de", 1),
        (b"abbbc", b"bbb", b"d", None),
        (b"cbc", b"b", b"de", -1),
        (b"dbc", b"b", b"de", 0),
        (b"ebbbcbbb", b"bbb", b"de", 1),
        (bytearray(b"abbbc"), b"bbb", b"d", None),
        (
            bytearray(b"cbc"),
            b"b",
            b"de",
            -1,
        ),
        (
            bytearray(b"dbc"),
            b"b",
            b"de",
            0,
        ),
        (
            bytearray(b"ebbbcbbb"),
            b"bbb",
            b"de",
            1,
        ),
    ],
)
def test_replace_result(origstr, substr, replstr, maxcount):
    _test_replace_result(None, origstr, substr, replstr, maxcount, False, False, str)


@pytest.mark.parametrize(
    "origstr",
    [
        "",
        "  ",
        "a",
        "aaa",
        "abaacaaadaaaa",
        '""',
    ],
)
@pytest.mark.parametrize(
    "substr",
    [
        "",
        "a",
        "aaa",
        '"',
    ],
)
@pytest.mark.parametrize(
    "replstr",
    [
        "",
        "a",
        "aaa",
        '"',
    ],
)
@pytest.mark.parametrize("maxcount", [None, -2, -1, 0, 1, 2, 3, 4, 5, 1000000000])
@pytest.mark.parametrize("should_be_tainted_origstr", [False, True])
@pytest.mark.parametrize("should_be_tainted_replstr", [False, True])
@pytest.mark.parametrize("str_type", [str, bytes, bytearray])
def test_replace_result_str(
    origstr, substr, replstr, maxcount, should_be_tainted_origstr, should_be_tainted_replstr, str_type
):
    _test_replace_result(
        None, origstr, substr, replstr, maxcount, should_be_tainted_origstr, should_be_tainted_replstr, str_type
    )


@pytest.mark.parametrize(
    "origstr",
    [
        "",
        "----",
    ],
)
@pytest.mark.parametrize(
    "substr",
    [
        b"",
        bytearray(b""),
        b"\xe1\xe3\xe9\xf7\xfa \xee\xe5\xf6\xf8",
    ],
)
@pytest.mark.parametrize(
    "replstr",
    [
        b"",
        b"\x83v\x83\x8d\x83_\x83N\x83g\x83e\x83X\x83g",
    ],
)
@pytest.mark.parametrize("maxcount", [None, -1, 0, 1, 2, 1000000000])
@pytest.mark.parametrize("should_be_tainted_origstr", [False, True])
@pytest.mark.parametrize("should_be_tainted_replstr", [False, True])
def test_replace_result_mix1(origstr, substr, replstr, maxcount, should_be_tainted_origstr, should_be_tainted_replstr):
    _test_replace_result(
        TypeError, origstr, substr, replstr, maxcount, should_be_tainted_origstr, should_be_tainted_replstr, str
    )


@pytest.mark.parametrize(
    "origstr",
    [
        b"",
        b"----",
    ],
)
@pytest.mark.parametrize(
    "substr",
    [
        "",
        "\xe9\xe7\xe0\xf1\xd4\xcb",
    ],
)
@pytest.mark.parametrize(
    "replstr",
    [
        "",
        "\xc3\xa9\xc3\xa7",
    ],
)
@pytest.mark.parametrize("maxcount", [None, -2, -1, 0, 1, 1000000000])
@pytest.mark.parametrize("should_be_tainted_origstr", [False, True])
@pytest.mark.parametrize("should_be_tainted_replstr", [False, True])
def test_replace_result_mix2(origstr, substr, replstr, maxcount, should_be_tainted_origstr, should_be_tainted_replstr):
    _test_replace_result(
        TypeError, origstr, substr, replstr, maxcount, should_be_tainted_origstr, should_be_tainted_replstr, str
    )


@pytest.mark.parametrize(
    "origstr",
    [
        bytearray(b""),
        bytearray(b"----"),
    ],
)
@pytest.mark.parametrize(
    "substr",
    [
        "",
        "\xc3\xa9\xc3\xa7",
    ],
)
@pytest.mark.parametrize(
    "replstr",
    [
        "",
        "\xc3\xa9\xc3\xa7",
    ],
)
@pytest.mark.parametrize("maxcount", [None, -1, 0, 1, 2, 1000000000])
@pytest.mark.parametrize("should_be_tainted_origstr", [False, True])
@pytest.mark.parametrize("should_be_tainted_replstr", [False, True])
def test_replace_result_mix3(origstr, substr, replstr, maxcount, should_be_tainted_origstr, should_be_tainted_replstr):
    _test_replace_result(
        TypeError, origstr, substr, replstr, maxcount, should_be_tainted_origstr, should_be_tainted_replstr, str
    )


@pytest.mark.parametrize(
    "origstr, substr, replstr, maxcount, formatted",
    [
        (
            "abbbc",
            "bbb",
            "d",
            None,
            ":+-<test_replace_tainted_orig>a<test_replace_tainted_orig>-+:d:+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+:",  # noqa: E501
        ),
        (
            "cbc",
            "b",
            "de",
            -1,
            ":+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+:de:+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+:",  # noqa: E501
        ),
        ("dbc", "b", "de", 0, ":+-<test_replace_tainted_orig>dbc<test_replace_tainted_orig>-+:"),
        (
            "ebbbcbbb",
            "bbb",
            "de",
            1,
            ":+-<test_replace_tainted_orig>e<test_replace_tainted_orig>-+:de:+-<test_replace_tainted_orig>cbbb<test_replace_tainted_orig>-+:",  # noqa: E501
        ),
        (
            "fbbbcbbb",
            "bbb",
            "de",
            3,
            ":+-<test_replace_tainted_orig>f<test_replace_tainted_orig>-+:de:+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+:de",  # noqa: E501
        ),
        (
            "gbcd",
            "gbcd",
            "y",
            -1,
            "y",
        ),
        (
            "hbcd",
            ":",
            "-",
            -1,
            ":+-<test_replace_tainted_orig>hbcd<test_replace_tainted_orig>-+:",
        ),
        (
            b"abbbc",
            b"bbb",
            b"d",
            None,
            b":+-<test_replace_tainted_orig>a<test_replace_tainted_orig>-+:d:+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+:",  # noqa: E501
        ),
        (
            b"cbc",
            b"b",
            b"de",
            -1,
            b":+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+:de:+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+:",  # noqa: E501
        ),
        (b"dbc", b"b", b"de", 0, b":+-<test_replace_tainted_orig>dbc<test_replace_tainted_orig>-+:"),
        (
            b"ebbbcbbb",
            b"bbb",
            b"de",
            1,
            b":+-<test_replace_tainted_orig>e<test_replace_tainted_orig>-+:de:+-<test_replace_tainted_orig>cbbb<test_replace_tainted_orig>-+:",  # noqa: E501
        ),
        (
            b"fbbbcbbb",
            b"bbb",
            b"de",
            3,
            b":+-<test_replace_tainted_orig>f<test_replace_tainted_orig>-+:de:+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+:de",  # noqa: E501
        ),
        (
            b"gbcd",
            b"gbcd",
            b"y",
            -1,
            b"y",
        ),
        (
            b"hbcd",
            b":",
            b"-",
            -1,
            b":+-<test_replace_tainted_orig>hbcd<test_replace_tainted_orig>-+:",
        ),
        (
            bytearray(b"abbbc"),
            b"bbb",
            b"d",
            None,
            bytearray(
                b":+-<test_replace_tainted_orig>a<test_replace_tainted_orig>-+:d:+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+:"  # noqa: E501
            ),
        ),
        (
            bytearray(b"cbc"),
            b"b",
            b"de",
            -1,
            bytearray(
                b":+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+:de:+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+:"  # noqa: E501
            ),
        ),
        (
            bytearray(b"dbc"),
            b"b",
            b"de",
            0,
            bytearray(b":+-<test_replace_tainted_orig>dbc<test_replace_tainted_orig>-+:"),  # noqa: E501
        ),
        (
            bytearray(b"ebbbcbbb"),
            b"bbb",
            b"de",
            1,
            bytearray(
                b":+-<test_replace_tainted_orig>e<test_replace_tainted_orig>-+:de:+-<test_replace_tainted_orig>cbbb<test_replace_tainted_orig>-+:"  # noqa: E501
            ),
        ),
        (
            bytearray(b"fbbbcbbb"),
            b"bbb",
            b"de",
            3,
            bytearray(
                b":+-<test_replace_tainted_orig>f<test_replace_tainted_orig>-+:de:+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+:de"  # noqa: E501
            ),
        ),
        (
            bytearray(b"gbcd"),
            b"gbcd",
            b"y",
            -1,
            bytearray(b"y"),
        ),
        (
            bytearray(b"hbcd"),
            b":",
            b"-",
            -1,
            bytearray(b":+-<test_replace_tainted_orig>hbcd<test_replace_tainted_orig>-+:"),
        ),
    ],
)
def test_replace_tainted_orig(origstr, substr, replstr, maxcount, formatted):
    origstr = taint_pyobject(
        pyobject=origstr,
        source_name="test_replace_tainted_orig",
        source_value=origstr,
        source_origin=OriginType.PARAMETER,
    )

    if maxcount is None:
        replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr)
        assert replaced == origstr.replace(substr, replstr)
    else:
        replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr, maxcount)
        assert replaced == origstr.replace(substr, replstr, maxcount)

    assert is_pyobject_tainted(replaced) is (replaced not in ("y", b"y", bytearray(b"y")))
    assert as_formatted_evidence(replaced) == formatted


@pytest.mark.parametrize(
    "origstr, substr, replstr, maxcount, formatted",
    [
        ("abbbc", "bbb", "d", None, "a:+-<test_replace_tainted_replstr>d<test_replace_tainted_replstr>-+:c"),
        ("cbc", "b", "de", -1, "c:+-<test_replace_tainted_replstr>de<test_replace_tainted_replstr>-+:c"),
        ("dbc", "b", "de", 0, "dbc"),
        (
            "ebbbcbbb",
            "bbb",
            "de",
            1,
            "e:+-<test_replace_tainted_replstr>de<test_replace_tainted_replstr>-+:cbbb",
        ),
        (
            b"abbbc",
            b"bbb",
            b"d",
            None,
            b"a:+-<test_replace_tainted_replstr>d<test_replace_tainted_replstr>-+:c",  # noqa: E501
        ),
        (b"cbc", b"b", b"de", -1, b"c:+-<test_replace_tainted_replstr>de<test_replace_tainted_replstr>-+:c"),
        (b"dbc", b"b", b"de", 0, b"dbc"),
        (
            b"ebbbcbbb",
            b"bbb",
            b"de",
            1,
            b"e:+-<test_replace_tainted_replstr>de<test_replace_tainted_replstr>-+:cbbb",
        ),
        (
            bytearray(b"abbbc"),
            b"bbb",
            b"d",
            None,
            bytearray(b"a:+-<test_replace_tainted_replstr>d<test_replace_tainted_replstr>-+:c"),  # noqa: E501
        ),
        (
            bytearray(b"cbc"),
            b"b",
            b"de",
            -1,
            bytearray(b"c:+-<test_replace_tainted_replstr>de<test_replace_tainted_replstr>-+:c"),  # noqa: E501
        ),
        (bytearray(b"dbc"), b"b", b"de", 0, bytearray(b"dbc")),
        (
            bytearray(b"ebbbcbbb"),
            b"bbb",
            b"de",
            1,
            bytearray(b"e:+-<test_replace_tainted_replstr>de<test_replace_tainted_replstr>-+:cbbb"),
        ),
    ],
)
def test_replace_tainted_replstr(origstr, substr, replstr, maxcount, formatted):
    replstr = taint_pyobject(
        pyobject=replstr,
        source_name="test_replace_tainted_replstr",
        source_value=replstr,
        source_origin=OriginType.PARAMETER,
    )

    if maxcount is None:
        replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr)
        assert replaced == origstr.replace(substr, replstr)
    else:
        replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr, maxcount)
        assert replaced == origstr.replace(substr, replstr, maxcount)

    assert is_pyobject_tainted(replaced) is (maxcount != 0)
    assert as_formatted_evidence(replaced) == formatted


@pytest.mark.parametrize(
    "origstr, substr, replstr, maxcount, formatted",
    [
        (
            "abbbc",
            "bbb",
            "d",
            None,
            ":+-<test_replace_tainted_orig>a<test_replace_tainted_orig>-+::+-<test_replace_tainted_replstr>d<test_replace_tainted_replstr>-+::+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+:",
        ),
        (
            "cbc",
            "b",
            "de",
            -1,
            ":+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+::+-<test_replace_tainted_replstr>de<test_replace_tainted_replstr>-+::+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+:",
        ),
        ("dbc", "b", "de", 0, ":+-<test_replace_tainted_orig>dbc<test_replace_tainted_orig>-+:"),
        (
            "ebbbcbbb",
            "bbb",
            "de",
            1,
            ":+-<test_replace_tainted_orig>e<test_replace_tainted_orig>-+::+-<test_replace_tainted_replstr>de<test_replace_tainted_replstr>-+::+-<test_replace_tainted_orig>cbbb<test_replace_tainted_orig>-+:",
        ),
        (
            "fbbbcbbb",
            "bbb",
            "de",
            3,
            ":+-<test_replace_tainted_orig>f<test_replace_tainted_orig>-+::+-<test_replace_tainted_replstr>de<test_replace_tainted_replstr>-+::+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+::+-<test_replace_tainted_replstr>de<test_replace_tainted_replstr>-+:",
        ),
        ("gbcd", "gbcd", "y", -1, ":+-<test_replace_tainted_replstr>y<test_replace_tainted_replstr>-+:"),
        (
            b"abbbc",
            b"bbb",
            b"d",
            None,
            b":+-<test_replace_tainted_orig>a<test_replace_tainted_orig>-+::+-<test_replace_tainted_replstr>d<test_replace_tainted_replstr>-+::+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+:",
        ),
        (
            b"cbc",
            b"b",
            b"de",
            -1,
            b":+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+::+-<test_replace_tainted_replstr>de<test_replace_tainted_replstr>-+::+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+:",
        ),
        (b"dbc", b"b", b"de", 0, b":+-<test_replace_tainted_orig>dbc<test_replace_tainted_orig>-+:"),
        (
            b"ebbbcbbb",
            b"bbb",
            b"de",
            1,
            b":+-<test_replace_tainted_orig>e<test_replace_tainted_orig>-+::+-<test_replace_tainted_replstr>de<test_replace_tainted_replstr>-+::+-<test_replace_tainted_orig>cbbb<test_replace_tainted_orig>-+:",
        ),
        (
            b"fbbbcbbb",
            b"bbb",
            b"de",
            3,
            b":+-<test_replace_tainted_orig>f<test_replace_tainted_orig>-+::+-<test_replace_tainted_replstr>de<test_replace_tainted_replstr>-+::+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+::+-<test_replace_tainted_replstr>de<test_replace_tainted_replstr>-+:",
        ),
        (b"gbcd", b"gbcd", b"y", -1, b":+-<test_replace_tainted_replstr>y<test_replace_tainted_replstr>-+:"),
        (
            bytearray(b"abbbc"),
            b"bbb",
            b"d",
            None,
            bytearray(
                b":+-<test_replace_tainted_orig>a<test_replace_tainted_orig>-+::+-<test_replace_tainted_replstr>d<test_replace_tainted_replstr>-+::+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+:"
            ),
        ),
        (
            bytearray(b"cbc"),
            b"b",
            b"de",
            -1,
            bytearray(
                b":+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+::+-<test_replace_tainted_replstr>de<test_replace_tainted_replstr>-+::+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+:"
            ),
        ),
        (
            bytearray(b"dbc"),
            b"b",
            b"de",
            0,
            bytearray(b":+-<test_replace_tainted_orig>dbc<test_replace_tainted_orig>-+:"),  # noqa: E501
        ),
        (
            bytearray(b"ebbbcbbb"),
            b"bbb",
            b"de",
            1,
            bytearray(
                b":+-<test_replace_tainted_orig>e<test_replace_tainted_orig>-+::+-<test_replace_tainted_replstr>de<test_replace_tainted_replstr>-+::+-<test_replace_tainted_orig>cbbb<test_replace_tainted_orig>-+:"
            ),
        ),
        (
            bytearray(b"fbbbcbbb"),
            b"bbb",
            b"de",
            3,
            bytearray(
                b":+-<test_replace_tainted_orig>f<test_replace_tainted_orig>-+::+-<test_replace_tainted_replstr>de<test_replace_tainted_replstr>-+::+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+::+-<test_replace_tainted_replstr>de<test_replace_tainted_replstr>-+:"
            ),
        ),
        (
            bytearray(b"gbcd"),
            b"gbcd",
            b"y",
            -1,
            bytearray(b":+-<test_replace_tainted_replstr>y<test_replace_tainted_replstr>-+:"),  # noqa: E501
        ),
    ],
)
def test_replace_tainted_orig_and_repl(origstr, substr, replstr, maxcount, formatted):
    origstr = taint_pyobject(
        pyobject=origstr,
        source_name="test_replace_tainted_orig",
        source_value=origstr,
        source_origin=OriginType.PARAMETER,
    )
    replstr = taint_pyobject(
        pyobject=replstr,
        source_name="test_replace_tainted_replstr",
        source_value=replstr,
        source_origin=OriginType.PARAMETER,
    )

    if maxcount is None:
        replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr)
        assert replaced == origstr.replace(substr, replstr)
    else:
        replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr, maxcount)
        assert replaced == origstr.replace(substr, replstr, maxcount)

    assert is_pyobject_tainted(replaced) is True
    assert as_formatted_evidence(replaced) == formatted


@pytest.mark.parametrize(
    "origstr, substr, replstr, maxcount, formatted",
    [
        (
            "abaabbcaaa",
            "a",
            "",
            None,
            "bbbc",
        ),
        (
            b"abaabbcaaa",
            b"a",
            b"",
            None,
            b"bbbc",
        ),
        (
            bytearray(b"abaabbcaaa"),
            b"a",
            b"",
            None,
            bytearray(b"bbbc"),
        ),
    ],
)
def test_replace_tainted_results_in_no_tainted(origstr, substr, replstr, maxcount, formatted):
    set_ranges(
        origstr,
        (_build_sample_range(0, 1, "name"), _build_sample_range(2, 2, "name"), _build_sample_range(6, 3, "name")),
    )
    if maxcount is None:
        replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr)
        assert replaced == origstr.replace(substr, replstr)
    else:
        replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr, maxcount)
        assert replaced == origstr.replace(substr, replstr, maxcount)

    assert as_formatted_evidence(replaced) == formatted
    assert is_pyobject_tainted(replaced) is False


@pytest.mark.parametrize(
    "origstr,formatted",
    [
        ("/", ""),
        ("", ""),
        ("", ""),
        ("/waf/", "waf/"),
        ("waf/", "waf"),
        ("//waf/", "/waf/"),
        ("path/waf/", "pathwaf/"),
        ("a/:", "a:"),
        # TODO: this replace raises basic_string::substr: __pos (which is 4) > this->size() (which is 3)
        # ("a/:+-/", ":+-<joiner>a<joiner>-+::+-<joiner>a/<joiner>-+:"),
    ],
)
def test_replace_tainted_results_in_no_tainted_django(origstr, formatted):
    path_info = origstr.encode("iso-8859-1").decode()
    sep = taint_pyobject(pyobject="/", source_name="d", source_value="/", source_origin=OriginType.PARAMETER)
    replaced = ddtrace_aspects.replace_aspect(path_info.replace, 1, path_info, sep, "", 1)
    assert replaced == path_info.replace(sep, "", 1)

    assert as_formatted_evidence(replaced) == formatted
    assert is_pyobject_tainted(replaced) is False


@pytest.mark.parametrize(
    "origstr, substr, replstr, maxcount, formatted",
    [
        (
            "aaabaaabbcaaa",
            "aa",
            "z",
            None,
            "z:+-<name>a<name>-+:bz:+-<name>a<name>-+:bbcz:+-<name>a<name>-+:",
        ),
        (
            b"aaabaaabbcaaa",
            b"aa",
            b"z",
            None,
            b"z:+-<name>a<name>-+:bz:+-<name>a<name>-+:bbcz:+-<name>a<name>-+:",
        ),
        (
            bytearray(b"aaabaaabbcaaa"),
            b"aa",
            b"z",
            None,
            bytearray(b"z:+-<name>a<name>-+:bz:+-<name>a<name>-+:bbcz:+-<name>a<name>-+:"),
        ),
    ],
)
def test_replace_tainted_shrinking_ranges(origstr, substr, replstr, maxcount, formatted):
    set_ranges(
        origstr,
        (_build_sample_range(0, 3, "name"), _build_sample_range(4, 3, "name"), _build_sample_range(10, 3, "name")),
    )
    if maxcount is None:
        replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr)
        assert replaced == origstr.replace(substr, replstr)
    else:
        replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr, maxcount)
        assert replaced == origstr.replace(substr, replstr, maxcount)

    assert as_formatted_evidence(replaced) == formatted
    assert is_pyobject_tainted(replaced) is True


@pytest.mark.parametrize(
    "origstr, taint_len, substr, replstr, maxcount, formatted",
    [
        ("abcd", 3, "", "_", None, "_:+-<name>a<name>-+:_:+-<name>b<name>-+:_:+-<name>c<name>-+:_d_"),
        ("abcd", 3, "", "_", 1, "_:+-<name>abc<name>-+:d"),
        ("abcd", 3, "", "_", 2, "_:+-<name>a<name>-+:_:+-<name>bc<name>-+:d"),
        ("", 0, "", "_", 1, "" if PYTHON_VERSION_INFO < (3, 9) else "_"),
        ("", 0, "", "_", 2, "" if PYTHON_VERSION_INFO < (3, 9) else "_"),
        ("a", 1, "", "_", 1, "_:+-<name>a<name>-+:"),
        ("a", 1, "", "_", 2, "_:+-<name>a<name>-+:_"),
        ("a", 1, "", "_", 0, ":+-<name>a<name>-+:"),
        (b"abcd", 3, b"", b"_", None, b"_:+-<name>a<name>-+:_:+-<name>b<name>-+:_:+-<name>c<name>-+:_d_"),
        (b"abcd", 3, b"", b"_", -1, b"_:+-<name>a<name>-+:_:+-<name>b<name>-+:_:+-<name>c<name>-+:_d_"),
        (b"abcd", 3, b"", b"_", 3, b"_:+-<name>a<name>-+:_:+-<name>b<name>-+:_:+-<name>c<name>-+:d"),
        (b"abcd", 3, b"", b"_", 4, b"_:+-<name>a<name>-+:_:+-<name>b<name>-+:_:+-<name>c<name>-+:_d"),
        (b"abcd", 3, b"", b"_", 5, b"_:+-<name>a<name>-+:_:+-<name>b<name>-+:_:+-<name>c<name>-+:_d_"),
        (b"abcd", 3, b"", b"_", 1, b"_:+-<name>abc<name>-+:d"),
        (b"abcd", 3, b"", b"_", 2, b"_:+-<name>a<name>-+:_:+-<name>bc<name>-+:d"),
        (b"", 0, b"", b"_", 1, b"" if PYTHON_VERSION_INFO < (3, 9) else b"_"),
        (b"", 0, b"", b"_", 2, b"" if PYTHON_VERSION_INFO < (3, 9) else b"_"),
        (b"a", 1, b"", b"_", 1, b"_:+-<name>a<name>-+:"),
        (b"a", 1, b"", b"_", 2, b"_:+-<name>a<name>-+:_"),
        (b"a", 1, b"", b"_", 0, b":+-<name>a<name>-+:"),
        (
            bytearray(b"abcd"),
            3,
            b"",
            b"_",
            None,
            bytearray(b"_:+-<name>a<name>-+:_:+-<name>b<name>-+:_:+-<name>c<name>-+:_d_"),
        ),
        (bytearray(b"abcd"), 3, b"", b"_", 1, bytearray(b"_:+-<name>abc<name>-+:d")),
        (bytearray(b"abcd"), 3, b"", b"_", 2, bytearray(b"_:+-<name>a<name>-+:_:+-<name>bc<name>-+:d")),
        (bytearray(b""), 0, b"", b"_", 1, bytearray(b"") if PYTHON_VERSION_INFO < (3, 9) else bytearray(b"_")),
        (bytearray(b""), 0, b"", b"_", 2, bytearray(b"") if PYTHON_VERSION_INFO < (3, 9) else bytearray(b"_")),
        (bytearray(b"a"), 1, b"", b"_", 1, bytearray(b"_:+-<name>a<name>-+:")),
        (bytearray(b"a"), 1, b"", b"_", 2, bytearray(b"_:+-<name>a<name>-+:_")),
        (bytearray(b"a"), 1, b"", b"_", 0, bytearray(b":+-<name>a<name>-+:")),
    ],
)
def test_replace_aspect_more(origstr, taint_len, substr, replstr, maxcount, formatted):
    if taint_len > 0:
        set_ranges(
            origstr,
            (_build_sample_range(0, taint_len, "name"),),
        )
    if maxcount is None:
        replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr)
        assert replaced == origstr.replace(substr, replstr)
    else:
        replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr, maxcount)
        assert replaced == origstr.replace(substr, replstr, maxcount)

    assert as_formatted_evidence(replaced) == formatted


@pytest.mark.parametrize(
    "origstr, substr, replstr, maxcount",
    [
        ("abcd", "a", None, None),
        (
            b"abcd",
            b"a",
            None,
            None,
        ),
        (
            bytearray(b"abcd"),
            b"a",
            None,
            None,
        ),
        ("abcd", None, "_", None),
        (
            b"abcd",
            None,
            b"_",
            None,
        ),
        (
            bytearray(b"abcd"),
            None,
            b"_",
            None,
        ),
    ],
)
def test_replace_aspect_typeerror(origstr, substr, replstr, maxcount):
    with pytest.raises(TypeError):
        if maxcount is None:
            ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr)
        else:
            ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr, maxcount)
