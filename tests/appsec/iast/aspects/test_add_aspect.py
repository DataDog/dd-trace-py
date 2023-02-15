#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
import pytest
from six import PY2


@pytest.mark.parametrize(
    "obj1, obj2",
    [
        (3.5, 3.3),
        (complex(2, 1), complex(3, 4)),
        (u"Hello ", u"world"),
        ("🙀", "🙀"),
        (b"Hi", b""),
        (["a"], ["b"]),
        (bytearray("a", "utf-8"), bytearray("b", "utf-8")),
        (("a", "b"), ("c", "d")),
    ],
)
@pytest.mark.skipif(PY2, reason="Python 3 only")
def test_add_aspect_successful(obj1, obj2):
    import ddtrace.appsec.iast._ast.aspects as ddtrace_aspects

    assert ddtrace_aspects.add_aspect(obj1, obj2) == obj1 + obj2


@pytest.mark.parametrize(
    "obj1, obj2",
    [(b"Hi", ""), ("Hi", b""), ({"a", "b"}, {"c", "d"}), (dict(), dict())],
)
@pytest.mark.skipif(PY2, reason="Python 3 only")
def test_add_aspect_type_error(obj1, obj2):
    import ddtrace.appsec.iast._ast.aspects as ddtrace_aspects

    with pytest.raises(TypeError) as e_info1:
        obj1 + obj2

    with pytest.raises(TypeError) as e_info2:
        ddtrace_aspects.add_aspect(obj1, obj2)

    assert str(e_info2.value) == str(e_info1.value)


@pytest.mark.parametrize(
    "obj1, obj2",
    [
        (3.5, 3.3),
        (complex(2, 1), complex(3, 4)),
        (u"Hello ", u"world"),
        ("🙀", "🙀"),
        (b"Hi", b""),
        (["a"], ["b"]),
        (bytearray("a", "utf-8"), bytearray("b", "utf-8")),
        (("a", "b"), ("c", "d")),
    ],
)
@pytest.mark.skipif(PY2, reason="Python 3 only")
def test_add_aspect_tainting_left_hand(obj1, obj2):
    import ddtrace.appsec.iast._ast.aspects as ddtrace_aspects
    from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted
    from ddtrace.appsec.iast._taint_tracking import taint_pyobject

    should_be_tainted = False
    if isinstance(obj1, (str, bytes, bytearray)):
        should_be_tainted = True
        taint_pyobject(obj1)

    result = ddtrace_aspects.add_aspect(obj1, obj2)
    assert is_pyobject_tainted(result) == should_be_tainted

    assert ddtrace_aspects.add_aspect(obj1, obj2) == obj1 + obj2


@pytest.mark.parametrize(
    "obj1, obj2",
    [
        (3.5, 3.3),
        (complex(2, 1), complex(3, 4)),
        (u"Hello ", u"world"),
        ("🙀", "🙀"),
        (b"Hi", b""),
        (["a"], ["b"]),
        (bytearray("a", "utf-8"), bytearray("b", "utf-8")),
        (("a", "b"), ("c", "d")),
    ],
)
@pytest.mark.skipif(PY2, reason="Python 3 only")
def test_add_aspect_tainting_right_hand(obj1, obj2):
    import ddtrace.appsec.iast._ast.aspects as ddtrace_aspects
    from ddtrace.appsec.iast._taint_tracking import clear_taint_mapping
    from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted
    from ddtrace.appsec.iast._taint_tracking import taint_pyobject

    clear_taint_mapping()
    should_be_tainted = False
    if isinstance(obj2, (str, bytes, bytearray)):
        should_be_tainted = True
        taint_pyobject(obj2)

    result = ddtrace_aspects.add_aspect(obj1, obj2)
    assert is_pyobject_tainted(result) == should_be_tainted

    assert ddtrace_aspects.add_aspect(obj1, obj2) == obj1 + obj2
