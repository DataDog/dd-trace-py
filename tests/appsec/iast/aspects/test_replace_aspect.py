#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import as_formatted_evidence
from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking import set_ranges
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects


def _build_sample_range(start, end, name):  # type: (int, int) -> TaintRange
    return TaintRange(start, end, Source(name, "sample_value", OriginType.PARAMETER))


@pytest.mark.parametrize(
    "origstr, substr, replstr, maxcount, expected",
    [
        ("abbbc", "bbb", "d", None, "adc"),
        ("cbc", "b", "de", -1, "cdec"),
        ("dbc", "b", "de", 0, "dbc"),
        ("ebbbcbbb", "bbb", "de", 1, "edecbbb"),
    ],
)
def test_replace_result(origstr, substr, replstr, maxcount, expected):
    if maxcount is None:
        replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr)
    else:
        replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr, maxcount)

    assert replaced == expected


@pytest.mark.parametrize(
    "origstr, substr, replstr, maxcount, expected, formatted",
    [
        (
            "abbbc",
            "bbb",
            "d",
            None,
            "adc",
            ":+-<test_replace_tainted_orig>a<test_replace_tainted_orig>-+:d:+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+:",  # noqa: E501
        ),
        (
            "cbc",
            "b",
            "de",
            -1,
            "cdec",
            ":+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+:de:+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+:",  # noqa: E501
        ),
        ("dbc", "b", "de", 0, "dbc", ":+-<test_replace_tainted_orig>dbc<test_replace_tainted_orig>-+:"),
        (
            "ebbbcbbb",
            "bbb",
            "de",
            1,
            "edecbbb",
            ":+-<test_replace_tainted_orig>e<test_replace_tainted_orig>-+:de:+-<test_replace_tainted_orig>cbbb<test_replace_tainted_orig>-+:",  # noqa: E501
        ),
        (
            "fbbbcbbb",
            "bbb",
            "de",
            3,
            "fdecde",
            ":+-<test_replace_tainted_orig>f<test_replace_tainted_orig>-+:de:+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+:de",  # noqa: E501
        ),
        (
            "gbcd",
            "gbcd",
            "y",
            -1,
            "y",
            "y",
        ),
        (
            "gbcd",
            ":",
            "-",
            -1,
            "gbcd",
            ":+-<test_replace_tainted_orig>gbcd<test_replace_tainted_orig>-+:",
        ),
    ],
)
def test_replace_tainted_orig(origstr, substr, replstr, maxcount, expected, formatted):
    origstr = taint_pyobject(
        pyobject=origstr,
        source_name="test_replace_tainted_orig",
        source_value=origstr,
        source_origin=OriginType.PARAMETER,
    )

    if maxcount is None:
        replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr)
    else:
        replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr, maxcount)

    assert replaced == expected
    assert is_pyobject_tainted(replaced) is (replaced != "y")
    assert as_formatted_evidence(replaced) == formatted


@pytest.mark.parametrize(
    "origstr, substr, replstr, maxcount, expected, formatted",
    [
        ("abbbc", "bbb", "d", None, "adc", "a:+-<test_replace_tainted_replstr>d<test_replace_tainted_replstr>-+:c"),
        ("cbc", "b", "de", -1, "cdec", "c:+-<test_replace_tainted_replstr>de<test_replace_tainted_replstr>-+:c"),
        ("dbc", "b", "de", 0, "dbc", "dbc"),
        (
            "ebbbcbbb",
            "bbb",
            "de",
            1,
            "edecbbb",
            "e:+-<test_replace_tainted_replstr>de<test_replace_tainted_replstr>-+:cbbb",
        ),
    ],
)
def test_replace_tainted_replstr(origstr, substr, replstr, maxcount, expected, formatted):
    replstr = taint_pyobject(
        pyobject=replstr,
        source_name="test_replace_tainted_replstr",
        source_value=replstr,
        source_origin=OriginType.PARAMETER,
    )

    if maxcount is None:
        replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr)
    else:
        replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr, maxcount)

    assert replaced == expected
    assert is_pyobject_tainted(replaced) is (maxcount != 0)
    assert as_formatted_evidence(replaced) == formatted


@pytest.mark.parametrize(
    "origstr, substr, replstr, maxcount, expected, formatted",
    [
        (
            "abbbc",
            "bbb",
            "d",
            None,
            "adc",
            ":+-<test_replace_tainted_orig>a<test_replace_tainted_orig>-+::+-<test_replace_tainted_replstr>d<test_replace_tainted_replstr>-+::+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+:",
        ),
        (
            "cbc",
            "b",
            "de",
            -1,
            "cdec",
            ":+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+::+-<test_replace_tainted_replstr>de<test_replace_tainted_replstr>-+::+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+:",
        ),
        ("dbc", "b", "de", 0, "dbc", ":+-<test_replace_tainted_orig>dbc<test_replace_tainted_orig>-+:"),
        (
            "ebbbcbbb",
            "bbb",
            "de",
            1,
            "edecbbb",
            ":+-<test_replace_tainted_orig>e<test_replace_tainted_orig>-+::+-<test_replace_tainted_replstr>de<test_replace_tainted_replstr>-+::+-<test_replace_tainted_orig>cbbb<test_replace_tainted_orig>-+:",
        ),
        (
            "fbbbcbbb",
            "bbb",
            "de",
            3,
            "fdecde",
            ":+-<test_replace_tainted_orig>f<test_replace_tainted_orig>-+::+-<test_replace_tainted_replstr>de<test_replace_tainted_replstr>-+::+-<test_replace_tainted_orig>c<test_replace_tainted_orig>-+::+-<test_replace_tainted_replstr>de<test_replace_tainted_replstr>-+:",
        ),
        ("gbcd", "gbcd", "y", -1, "y", ":+-<test_replace_tainted_replstr>y<test_replace_tainted_replstr>-+:"),
    ],
)
def test_replace_tainted_orig_and_repl(origstr, substr, replstr, maxcount, expected, formatted):
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
    else:
        replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr, maxcount)

    assert replaced == expected
    assert is_pyobject_tainted(replaced) is True
    assert as_formatted_evidence(replaced) == formatted


@pytest.mark.parametrize(
    "origstr, substr, replstr, maxcount, expected, formatted",
    [
        (
            "ababbca",
            "a",
            "",
            None,
            "bbbc",
            "bbbc",
        ),
    ],
)
def test_replace_tainted_results_in_no_tainted(origstr, substr, replstr, maxcount, expected, formatted):
    set_ranges(
        origstr,
        (_build_sample_range(0, 1, "name"), _build_sample_range(2, 1, "name"), _build_sample_range(5, 1, "name")),
    )
    if maxcount is None:
        replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr)
    else:
        replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr, maxcount)

    assert replaced == expected
    assert as_formatted_evidence(replaced) == formatted
    assert is_pyobject_tainted(replaced) is False


@pytest.mark.parametrize(
    "origstr, substr, replstr, maxcount, expected, formatted",
    [
        (
            "aaabaaabbcaaa",
            "aa",
            "z",
            None,
            "zabzabbcza",
            "z:+-<name>a<name>-+:bz:+-<name>a<name>-+:bbcz:+-<name>a<name>-+:",
        ),
    ],
)
def test_replace_tainted_shrinking_ranges(origstr, substr, replstr, maxcount, expected, formatted):
    set_ranges(
        origstr,
        (_build_sample_range(0, 3, "name"), _build_sample_range(4, 3, "name"), _build_sample_range(10, 3, "name")),
    )
    if maxcount is None:
        replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr)
    else:
        replaced = ddtrace_aspects.replace_aspect(origstr.replace, 1, origstr, substr, replstr, maxcount)

    assert replaced == expected
    assert as_formatted_evidence(replaced) == formatted
    assert is_pyobject_tainted(replaced) is True
