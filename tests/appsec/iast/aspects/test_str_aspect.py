#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
import sys

import pytest

from ddtrace.appsec.iast._input_info import Input_info


@pytest.mark.parametrize(
    "obj, kwargs",
    [
        (3.5, {}),
        ("Hi", {}),
        ("🙀", {}),
        (b"Hi", {}),
        (b"Hi", {"encoding": "utf-8", "errors": "strict"}),
        (b"Hi", {"encoding": "utf-8", "errors": "ignore"}),
        ({"a": "b", "c": "d"}, {}),
        ({"a", "b", "c", "d"}, {}),
        (("a", "b", "c", "d"), {}),
        (["a", "b", "c", "d"], {}),
    ],
)
@pytest.mark.skipif(sys.version_info < (3, 6, 0), reason="Python 3.6+ only")
def test_str_aspect(obj, kwargs):
    import ddtrace.appsec.iast._ast.aspects as ddtrace_aspects

    assert ddtrace_aspects.str_aspect(obj, **kwargs) == str(obj, **kwargs)


@pytest.mark.parametrize(
    "obj, kwargs, should_be_tainted",
    [
        (3.5, {}, False),
        (u"Hi", {}, True),
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
@pytest.mark.skipif(sys.version_info < (3, 6, 0), reason="Python 3.6+ only")
def test_str_aspect_tainting(obj, kwargs, should_be_tainted):
    import ddtrace.appsec.iast._ast.aspects as ddtrace_aspects
    from ddtrace.appsec.iast._taint_tracking import clear_taint_mapping
    from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted
    from ddtrace.appsec.iast._taint_tracking import setup
    from ddtrace.appsec.iast._taint_tracking import taint_pyobject

    setup(bytes.join, bytearray.join)
    clear_taint_mapping()
    if should_be_tainted:
        obj = taint_pyobject(obj, Input_info("test_str_aspect_tainting", obj, 0))

    result = ddtrace_aspects.str_aspect(obj, **kwargs)
    assert is_pyobject_tainted(result) == should_be_tainted

    assert result == str(obj, **kwargs)
