#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
import pytest

from ddtrace.appsec.iast._ast.aspects import str_aspect


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
def test_str_aspect(obj, kwargs):
    assert str_aspect(obj, **kwargs) == str(obj, **kwargs)
