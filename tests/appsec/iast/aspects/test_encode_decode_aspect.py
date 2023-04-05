#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
import sys

import pytest

from ddtrace.appsec.iast._input_info import Input_info


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
@pytest.mark.skipif(sys.version_info < (3, 6, 0), reason="Python 3.6+ only")
def test_decode_and_add_aspect(infix, args, kwargs, should_be_tainted, prefix, suffix):
    import ddtrace.appsec.iast._ast.aspects as ddtrace_aspects
    from ddtrace.appsec.iast._taint_dict import clear_taint_mapping
    from ddtrace.appsec.iast._taint_tracking import get_tainted_ranges
    from ddtrace.appsec.iast._taint_tracking import setup
    from ddtrace.appsec.iast._taint_tracking import taint_pyobject

    setup(bytes.join, bytearray.join)
    clear_taint_mapping()
    if should_be_tainted:
        infix = taint_pyobject(infix, Input_info("test_decode_aspect", infix, 0))

    main_string = ddtrace_aspects.add_aspect(prefix, infix)
    if should_be_tainted:
        assert len(get_tainted_ranges(main_string))
    main_string = ddtrace_aspects.add_aspect(main_string, suffix)
    if should_be_tainted:
        assert len(get_tainted_ranges(main_string))
    ok, res = catch_all(ddtrace_aspects.decode_aspect, (main_string,) + args, kwargs)
    assert (ok, res) == catch_all(main_string.__class__.decode, (main_string,) + args, kwargs)
    if should_be_tainted and ok:
        list_tr = get_tainted_ranges(res)
        assert len(list_tr) == 1
        assert list_tr[0][1] == len(prefix.decode(*args, **kwargs))
        # assert length of tainted is ok. If last char was replaced due to some missing bytes, it may be shorter.
        len_infix = len(infix.decode(*args, **kwargs))
        assert list_tr[0][2] == len_infix or (kwargs == {"errors": "replace"} and list_tr[0][2] == len_infix - 1)


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
@pytest.mark.skipif(sys.version_info < (3, 6, 0), reason="Python 3.6+ only")
def test_encode_and_add_aspect(infix, args, kwargs, should_be_tainted, prefix, suffix):
    import ddtrace.appsec.iast._ast.aspects as ddtrace_aspects
    from ddtrace.appsec.iast._taint_dict import clear_taint_mapping
    from ddtrace.appsec.iast._taint_tracking import get_tainted_ranges
    from ddtrace.appsec.iast._taint_tracking import setup
    from ddtrace.appsec.iast._taint_tracking import taint_pyobject

    setup(bytes.join, bytearray.join)
    clear_taint_mapping()
    if should_be_tainted:
        infix = taint_pyobject(infix, Input_info("test_decode_aspect", infix, 0))

    main_string = ddtrace_aspects.add_aspect(prefix, infix)
    if should_be_tainted:
        assert len(get_tainted_ranges(main_string))
    main_string = ddtrace_aspects.add_aspect(main_string, suffix)
    if should_be_tainted:
        assert len(get_tainted_ranges(main_string))
    ok, res = catch_all(ddtrace_aspects.encode_aspect, (main_string,) + args, kwargs)
    assert (ok, res) == catch_all(main_string.__class__.encode, (main_string,) + args, kwargs)
    if should_be_tainted and ok:
        list_tr = get_tainted_ranges(res)
        assert len(list_tr) == 1
        assert list_tr[0][1] == len(prefix.encode(*args, **kwargs))
        len_infix = len(infix.encode(*args, **kwargs))
        assert list_tr[0][2] == len_infix
