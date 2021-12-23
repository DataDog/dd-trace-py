import sys

from hypothesis import given
from hypothesis import strategies as st

from ddtrace.appsec._ddwaf import _Wrapper


PYTHON_OBJECTS = st.recursive(
    base=st.one_of(st.none(), st.booleans(), st.integers(), st.floats(), st.text()),
    extend=lambda inner: st.lists(inner) | st.dictionaries(st.text(), inner),
)

WRAPPER_KWARGS = dict(
    max_objects=st.integers(min_value=0, max_value=2 ** 63 - 1),
)


@given(obj=PYTHON_OBJECTS, kwargs=st.fixed_dictionaries(WRAPPER_KWARGS))
def test_ddwaf_objects_wrapper(obj, kwargs):
    obj = _Wrapper(obj, **kwargs)
    repr(obj)
    del obj


if __name__ == "__main__":
    import atheris

    atheris.Setup(sys.argv, atheris.instrument_func(test_ddwaf_objects_wrapper.hypothesis.fuzz_one_input))
    atheris.Fuzz()
