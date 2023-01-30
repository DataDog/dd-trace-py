#!/usr/bin/env python3
import pytest

from ddtrace.appsec.iast.ast.aspects import str_aspect


@pytest.mark.parametrize(
    "obj, encoding, errors",
    [
        (3.5, None, None),
        (u"Hi", None, None),
        (b"Hi", None, None),
        ("🙀", None, None),
        ({"a": "b", "c": "d"}, None, None),
        ({"a", "b", "c", "d"}, None, None),
        (("a", "b", "c", "d"), None, None),
        (["a", "b", "c", "d"], None, None),

    ],
)
def test_str_aspect(obj, encoding, errors):
    assert str_aspect(obj, encoding, errors) == str(obj, encoding, errors)
