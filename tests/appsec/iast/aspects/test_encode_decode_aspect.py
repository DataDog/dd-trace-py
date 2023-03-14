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
    "self",
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
@pytest.mark.skipif(sys.version_info < (3, 6, 0), reason="Python 3.6+ only")
def test_decode_aspect(self, args, kwargs, should_be_tainted):
    import ddtrace.appsec.iast._ast.aspects as ddtrace_aspects
    from ddtrace.appsec.iast._taint_tracking import clear_taint_mapping
    from ddtrace.appsec.iast._taint_tracking import get_tainted_ranges
    from ddtrace.appsec.iast._taint_tracking import taint_pyobject

    clear_taint_mapping()
    if should_be_tainted:
        self = taint_pyobject(self, Input_info("test_decode_aspect", self, 0))

    ok, res = catch_all(ddtrace_aspects.decode_aspect, (self,) + args, kwargs)
    assert (ok, res) == catch_all(self.__class__.decode, (self,) + args, kwargs)
    if should_be_tainted and ok:
        list_tr = get_tainted_ranges(res)
        assert len(list_tr) == 1
        assert list_tr[0][1] == 0
        # assert length of tainted is ok. If last char was replaced due to some missing bytes, it may be shorter.
        assert list_tr[0][2] == len(res) or (kwargs == {"errors": "replace"} and list_tr[0][2] == len(res) - 1)


@pytest.mark.parametrize(
    "self",
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
@pytest.mark.skipif(sys.version_info < (3, 6, 0), reason="Python 3.6+ only")
def test_encode_aspect(self, args, kwargs):
    import ddtrace.appsec.iast._ast.aspects as ddtrace_aspects

    assert catch_all(ddtrace_aspects.encode_aspect, (self,) + args, kwargs) == catch_all(
        self.__class__.encode, (self,) + args, kwargs
    )


# @pytest.mark.parametrize(
#     "obj, kwargs, should_be_tainted",
#     [
#         (3.5, {}, False),
#         ("Hi", {}, True),
#         ("🙀", {}, True),
#         (b"Hi", {}, True),
#         (bytearray(b"Hi"), {}, True),
#         (b"Hi", {"encoding": "utf-8", "errors": "strict"}, True),
#         (b"Hi", {"encoding": "utf-8", "errors": "ignore"}, True),
#         ({"a": "b", "c": "d"}, {}, False),
#         ({"a", "b", "c", "d"}, {}, False),
#         (("a", "b", "c", "d"), {}, False),
#         (["a", "b", "c", "d"], {}, False),
#     ],
# )
# @pytest.mark.skipif(sys.version_info < (3, 6, 0), reason="Python 3.6+ only")
# def test_str_aspect_tainting(obj, kwargs, should_be_tainted):
#     import ddtrace.appsec.iast._ast.aspects as ddtrace_aspects
#     from ddtrace.appsec.iast._taint_tracking import clear_taint_mapping
#     from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted
#     from ddtrace.appsec.iast._taint_tracking import setup
#     from ddtrace.appsec.iast._taint_tracking import taint_pyobject

#     setup(bytes.join, bytearray.join)
#     clear_taint_mapping()
#     if should_be_tainted:
#         obj = taint_pyobject(obj, Input_info("test_str_aspect_tainting", obj, 0))

#     result = ddtrace_aspects.str_aspect(obj, **kwargs)
#     assert is_pyobject_tainted(result) == should_be_tainted

#     assert result == str(obj, **kwargs)
