import sys

from hypothesis import given
from hypothesis import strategies as st
import pytest

from ddtrace.appsec._api_security.schema import build_schema


SCALAR_OBJECTS = st.one_of(st.none(), st.booleans(), st.integers(), st.floats(), st.characters())

PYTHON_OBJECTS = st.recursive(
    base=SCALAR_OBJECTS,
    extend=lambda inner: st.lists(inner) | st.dictionaries(SCALAR_OBJECTS, inner),
)


@given(obj=PYTHON_OBJECTS)
def test_build_schema(obj):
    obj = build_schema(obj)
    repr(obj)
    del obj


def equal_without_meta(t1, t2):
    if t1 is None or t2 is None:
        print("NONE")
        return False
    return equal_value(t1[0], t2[0])


def equal_value(t1, t2):
    if isinstance(t1, list) and isinstance(t2, list):
        return len(t1) == len(t2) and all(equal_without_meta(a, b) for a, b in zip(t1, t2))
    if isinstance(t1, dict) and isinstance(t2, dict):
        return len(t1) == len(t2) and all(equal_without_meta(t1[k], t2.get(k)) for k in t1)
    if isinstance(t1, int) and isinstance(t2, int):
        return t1 == t2
    return False


@pytest.mark.parametrize(
    "obj, res",
    [
        (32, [4]),
        (True, [2]),
        ("test", [8]),
        (b"test", [8]),
        (1.0, [16]),
        ([1, 2], [[[4]], {"len": 2}]),
        ({"test": "truc"}, [{"test": [8]}]),
        (None, [1]),
    ],
)
def test_small_schemas(obj, res):
    assert equal_without_meta(build_schema(obj), res)


@pytest.mark.skipif(sys.version_info[:2] < (3, 6), reason="dict iteration order is different in python <= 3.5")
@pytest.mark.parametrize(
    "obj, res",
    [
        (324, [4]),
        (True, [2]),
        ([True, 2], [[[2]], {"len": 2, "truncated": True}]),
        ({"toast": "touch", "tomato": "tommy"}, [{"toast": [8]}, {"truncated": True}]),
        ({"foo": {"bar": 42}}, [{"foo": [{"bar": [0]}]}]),
    ],
)
def test_limits(obj, res):
    assert equal_without_meta(build_schema(obj, max_depth=2, max_girth=1, max_types_in_array=1), res)
