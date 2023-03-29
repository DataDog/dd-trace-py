import sys

from hypothesis import given
from hypothesis import strategies as st
import pytest

from ddtrace.appsec.ddwaf.ddwaf_types import ddwaf_object


SCALAR_OBJECTS = st.one_of(st.none(), st.booleans(), st.integers(), st.floats(), st.characters())

PYTHON_OBJECTS = st.recursive(
    base=SCALAR_OBJECTS,
    extend=lambda inner: st.lists(inner) | st.dictionaries(SCALAR_OBJECTS, inner),
)

WRAPPER_KWARGS = dict(
    max_objects=st.integers(min_value=0, max_value=(1 << 63) - 1),
)


@given(obj=PYTHON_OBJECTS, kwargs=st.fixed_dictionaries(WRAPPER_KWARGS))
def test_ddwaf_objects_wrapper(obj, kwargs):
    obj = ddwaf_object(obj, **kwargs)
    repr(obj)
    del obj


class _AnyObject:
    cst = "1048A9B04F0EDC"

    def __str__(self):
        return self.cst


@pytest.mark.parametrize(
    "obj, res",
    [
        (32, "32"),
        (True, True),
        ("test", "test"),
        (b"test", "test"),
        (1.0, "1.0"),
        ([1, 2], ["1", "2"]),
        ({"test": "truc"}, {"test": "truc"}),
        (None, None),
        (_AnyObject(), _AnyObject.cst),
    ],
)
def test_small_objects(obj, res):
    dd_obj = ddwaf_object(obj)
    assert dd_obj.struct == res


@pytest.mark.parametrize(
    "obj, res",
    [
        (324, "324"),  # integers are formatted into strings by libddwaf and are not truncated
        (True, True),
        ("toast", "to"),
        (b"toast", "to"),
        (1.034, "1."),
        ([1, 2], ["1"]),
        ({"toast": "touch", "tomato": "tommy"}, {"to": "to"}),
        (None, None),
        (_AnyObject(), _AnyObject.cst[:2]),
        ([[[1, 2], 3], 4], [[]]),
    ],
)
def test_limits(obj, res):
    # truncation of max_string_length takes the last C null byte into account
    dd_obj = ddwaf_object(obj, max_objects=1, max_depth=1, max_string_length=3)
    assert dd_obj.struct == res


if __name__ == "__main__":
    import atheris

    atheris.Setup(sys.argv, atheris.instrument_func(test_ddwaf_objects_wrapper.hypothesis.fuzz_one_input))
    atheris.Fuzz()
