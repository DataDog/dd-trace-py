import pytest

from ddtrace.internal.safety import SafeObjectProxy


class BadError(Exception):
    """
    We should never see this.
    """


def unsafe_function():
    raise BadError("Bad side-effect")


class UnsafeObject(object):
    def __init__(self):
        self.safe_attr = 42

    @property
    def unsafe_property(self):
        unsafe_function()

    def __len__(self):
        unsafe_function()

    def unsafe_method(self):
        unsafe_function()

    @staticmethod
    def unsafe_static():
        unsafe_function()

    def __get__(self, obj, objtype=None):
        return unsafe_function()


@pytest.mark.parametrize(
    "value",
    [10, 10.0, None, False, dict(), list(), tuple(), set(), "Hello string"],
)
def test_safe_types(value):
    assert type(SafeObjectProxy.safe(value)) == type(value)


def test_safe_proxy():
    assert all(
        [isinstance(SafeObjectProxy.safe(v), SafeObjectProxy) for v in [unsafe_function, UnsafeObject(), UnsafeObject]]
    )


def test_safe_function_call():
    with pytest.raises(RuntimeError):
        SafeObjectProxy.safe(unsafe_function)()


def test_safe_eval_property():
    with pytest.raises(AttributeError):
        SafeObjectProxy.safe(UnsafeObject()).unsafe_property
