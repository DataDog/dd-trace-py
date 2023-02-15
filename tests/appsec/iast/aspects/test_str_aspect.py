#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
import pytest
from six import PY2

from ddtrace.appsec.iast.input_info import Input_info


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
@pytest.mark.skipif(PY2, reason="Python 3 only")
def test_str_aspect(obj, kwargs):
    import ddtrace.appsec.iast._ast.aspects as ddtrace_aspects

    assert ddtrace_aspects.str_aspect(obj, **kwargs) == str(obj, **kwargs)


@pytest.mark.parametrize(
    "obj, kwargs",
    [
        (3.5, {}),
        ("Hi", {}),
        ("🙀", {}),
        (b"Hi", {}),
        (bytearray(b"Hi"), {}),
        (b"Hi", {"encoding": "utf-8", "errors": "strict"}),
        (b"Hi", {"encoding": "utf-8", "errors": "ignore"}),
        ({"a": "b", "c": "d"}, {}),
        ({"a", "b", "c", "d"}, {}),
        (("a", "b", "c", "d"), {}),
        (["a", "b", "c", "d"], {}),
    ],
)
@pytest.mark.skipif(PY2, reason="Python 3 only")
def test_str_aspect_tainting(obj, kwargs):
    import ddtrace.appsec.iast._ast.aspects as ddtrace_aspects
    from ddtrace.appsec.iast._taint_tracking import clear_taint_mapping
    from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted
    from ddtrace.appsec.iast._taint_tracking import taint_pyobject

    clear_taint_mapping()
    should_be_tainted = False
    if isinstance(obj, (str, bytes, bytearray)):
        should_be_tainted = True
        taint_pyobject(obj, Input_info("test_str_aspect_tainting", obj, 0))

    result = ddtrace_aspects.str_aspect(obj, **kwargs)
    assert is_pyobject_tainted(result) == should_be_tainted

    assert result == str(obj, **kwargs)
