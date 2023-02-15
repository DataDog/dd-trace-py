#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
import pytest
from six import PY2


@pytest.mark.parametrize(
    "obj, kwargs",
    [
        (3.5, {}),
        (u"Hi", {}),
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
        (u"Hi", {}),
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
def test_str_aspect_tainting(obj, kwargs):
    import ddtrace.appsec.iast._ast.aspects as ddtrace_aspects
    from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted
    from ddtrace.appsec.iast._taint_tracking import taint_pyobject

    should_be_tainted = False
    if isinstance(obj, (str, bytes)):
        should_be_tainted = True
        taint_pyobject(obj)

    result = ddtrace_aspects.str_aspect(obj, **kwargs)
    assert is_pyobject_tainted(result) == should_be_tainted

    assert result == str(obj, **kwargs)
