#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
import sys

import pytest


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
@pytest.mark.skipif(sys.version_info[0] < 3, reason="Python 3 only")
def test_str_aspect(obj, kwargs):
    from ddtrace.appsec.iast._ast.aspects import str_aspect

    assert str_aspect(obj, **kwargs) == str(obj, **kwargs)
