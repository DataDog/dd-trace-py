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


class UnsafeHashableObject(UnsafeObject):
    def __hash__(self):
        return id(self)


@pytest.mark.parametrize(
    "value",
    [10, 10.0, None, False, "Hello string", b"Hello bytes", 42j],
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


@pytest.mark.parametrize("_type", [list, set, tuple, frozenset])
def test_safe_collection(_type):
    # Ensure that all the items within a collection are wrapped by a safe object
    # proxy.
    assert all(isinstance(_, SafeObjectProxy) for _ in SafeObjectProxy.safe(_type([UnsafeObject()] * 10)))


def test_safe_dict():
    safe_dict = SafeObjectProxy.safe({UnsafeHashableObject(): UnsafeObject()})
    assert isinstance(safe_dict, dict)
    assert isinstance(safe_dict, SafeObjectProxy)

    assert all(isinstance(_, SafeObjectProxy) for kv in safe_dict.items() for _ in kv)

    with pytest.raises(TypeError):
        # For safety reasons we cannot use this form of dict iteration as the
        # keys are safe objects in general.
        assert all(isinstance(safe_dict[_], SafeObjectProxy) for _ in safe_dict)
