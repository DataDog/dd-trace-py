#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects
from tests.appsec.iast.aspects.conftest import _iast_patched_module


mod = _iast_patched_module("benchmarks.bm.iast_fixtures.str_methods")


def catch_all(fun, args, kwargs):
    try:
        return True, fun(*args, **kwargs)
    except BaseException as e:
        return False, (type(e), e.args)


@pytest.mark.parametrize(
    "infix",
    [
        b"ascii123",
        b"\xc3\xa9\xc3\xa7\xc3\xa0\xc3\xb1\xc3\x94\xc3\x8b",
        b"\xe9\xe7\xe0\xf1\xd4\xcb",
        b"\x83v\x83\x8d\x83_\x83N\x83g\x83e\x83X\x83g",
        b"\xe1\xe3\xe9\xf7\xfa \xee\xe5\xf6\xf8",
    ],
)
@pytest.mark.parametrize("args", [(), ("utf-8",), ("latin1",), ("iso-8859-8",), ("sjis",)])
@pytest.mark.parametrize("kwargs", [{}, {"errors": "ignore"}, {"errors": "strict"}, {"errors": "replace"}])
@pytest.mark.parametrize("should_be_tainted", [False, True])
@pytest.mark.parametrize("prefix", [b"", b"abc", b"\xc3\xa9\xc3\xa7"])
@pytest.mark.parametrize("suffix", [b"", b"abc", b"\xc3\xa9\xc3\xa7"])
def test_decode_and_add_aspect(infix, args, kwargs, should_be_tainted, prefix, suffix):
    import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects

    if should_be_tainted:
        infix = taint_pyobject(
            pyobject=infix,
            source_name="test_decode_aspect",
            source_value=repr(infix),
            source_origin=OriginType.PARAMETER,
        )

    main_string = ddtrace_aspects.add_aspect(prefix, infix)
    if should_be_tainted:
        assert len(get_tainted_ranges(main_string))
    main_string = ddtrace_aspects.add_aspect(main_string, suffix)
    if should_be_tainted:
        assert len(get_tainted_ranges(main_string))
    ok, res = catch_all(
        ddtrace_aspects.decode_aspect,
        (
            main_string.__class__.decode,
            1,
            main_string,
        )
        + args,
        kwargs,
    )
    assert (ok, res) == catch_all(
        main_string.__class__.decode,
        (main_string,) + args,
        kwargs,
    )
    should_be_tainted = (
        should_be_tainted
        and infix != b"\xc3\xa9\xc3\xa7\xc3\xa0\xc3\xb1\xc3\x94\xc3\x8b"
        and len(res) > (len(suffix) + len(prefix))
    )

    if should_be_tainted and ok:
        list_tr = get_tainted_ranges(res)
        assert len(list_tr) == 1
        assert list_tr[0].start == len(prefix.decode(*args, **kwargs))
        # assert length of tainted is ok. If last char was replaced due to some missing bytes, it may be shorter.
        len_infix = len(infix.decode(*args, **kwargs))
        assert list_tr[0].length == len_infix or (
            kwargs == {"errors": "replace"} and list_tr[0].length == len_infix - 1
        )


@pytest.mark.parametrize(
    "infix",
    [
        "ascii123",
        "éçàñÔË",
        "プロダクトテスト",
        "בדיקת מוצר",
        "😀😱💻❤️🏳️🐶",
    ],
)
@pytest.mark.parametrize("args", [(), ("utf-8",), ("latin1",), ("iso-8859-8",), ("sjis",)])
@pytest.mark.parametrize("kwargs", [{}, {"errors": "ignore"}, {"errors": "strict"}, {"errors": "replace"}])
@pytest.mark.parametrize("should_be_tainted", [False, True])
@pytest.mark.parametrize("prefix", ["", "abc", "èôï"])
@pytest.mark.parametrize("suffix", ["", "abc", "èôï"])
def test_encode_and_add_aspect(infix, args, kwargs, should_be_tainted, prefix, suffix):
    if should_be_tainted:
        infix = taint_pyobject(
            pyobject=infix, source_name="test_decode_aspect", source_value=infix, source_origin=OriginType.PARAMETER
        )

    main_string = ddtrace_aspects.add_aspect(prefix, infix)
    if should_be_tainted:
        assert len(get_tainted_ranges(main_string))
    main_string = ddtrace_aspects.add_aspect(main_string, suffix)
    if should_be_tainted:
        assert len(get_tainted_ranges(main_string))
    ok, res = catch_all(
        ddtrace_aspects.encode_aspect,
        (
            main_string.__class__.encode,
            1,
            main_string,
        )
        + args,
        kwargs,
    )

    assert (ok, res) == catch_all(main_string.__class__.encode, (main_string,) + args, kwargs)
    should_be_tainted = should_be_tainted and len(res) > (len(suffix) + len(prefix))
    if should_be_tainted and ok:
        list_ranges = get_tainted_ranges(res)
        assert len(list_ranges) == 1
        assert list_ranges[0].start == len(prefix.encode(*args, **kwargs))
        len_infix = len(infix.encode(*args, **kwargs))
        assert list_ranges[0].length == len_infix


def test_encode_error_with_tainted_gives_one_log_metric(telemetry_writer):
    string_input = taint_pyobject(
        pyobject="abcde",
        source_name="test_add_aspect_tainting_left_hand",
        source_value="abcde",
        source_origin=OriginType.PARAMETER,
    )
    with pytest.raises(LookupError):
        mod.do_encode(string_input, "encoding-not-exists")

    list_metrics_logs = list(telemetry_writer._logs)
    assert len(list_metrics_logs) == 0


def test_encode_error_with_not_tainted_gives_no_log_metric(telemetry_writer):
    string_input = "abcde"
    with pytest.raises(LookupError):
        mod.do_encode(string_input, "encoding-not-exists")

    list_metrics_logs = list(telemetry_writer._logs)
    assert len(list_metrics_logs) == 0


def test_dencode_error_with_tainted_gives_one_log_metric(telemetry_writer):
    string_input = taint_pyobject(
        pyobject=b"abcde",
        source_name="test_add_aspect_tainting_left_hand",
        source_value="abcde",
        source_origin=OriginType.PARAMETER,
    )
    with pytest.raises(LookupError):
        mod.do_decode(string_input, "decoding-not-exists")

    list_metrics_logs = list(telemetry_writer._logs)
    assert len(list_metrics_logs) == 0


def test_dencode_error_with_not_tainted_gives_no_log_metric(telemetry_writer):
    string_input = b"abcde"
    with pytest.raises(LookupError):
        mod.do_decode(string_input, "decoding-not-exists")

    list_metrics_logs = list(telemetry_writer._logs)
    assert len(list_metrics_logs) == 0
