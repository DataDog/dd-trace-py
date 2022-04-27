from typing import Any
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple
from typing import Type
from typing import Union

from ddtrace.internal.compat import BUILTIN
from ddtrace.internal.compat import CALLABLE_TYPES
from ddtrace.internal.utils.attrdict import AttrDict
from ddtrace.internal.utils.cache import cached
from ddtrace.vendor import wrapt


def _maybe_slots(obj):
    # type: (Any) -> Union[Tuple[str], List[str]]
    try:
        slots = object.__getattribute__(obj, "__slots__")
        if isinstance(slots, str):
            return (slots,)
        return slots
    except AttributeError:
        return []


@cached()
def _slots(_type):
    # type: (Type) -> Set[str]
    return {_ for cls in object.__getattribute__(_type, "__mro__") for _ in _maybe_slots(cls)}


def get_slots(obj):
    # type: (Any) -> Set[str]
    """Get the object's slots."""
    return _slots(type(obj))


def _isinstance(obj, types):
    # type: (Any, Union[Type, Tuple[Union[Type, Tuple[Any, ...]], ...]]) -> bool
    # DEV: isinstance falls back to calling __getattribute__ which could cause
    # side effects.
    return issubclass(type(obj), types)


class SafeObjectProxy(wrapt.ObjectProxy):
    """Object proxy to make sure we don't call unsafe code.

    Access to the wrapped object is denied to prevent any potential
    side-effects. Arbitrary objects are essentially converted into attribute
    dictionaries. Callable objects are made uncallable to prevent accidental
    calls that can also trigger side-effects.
    """

    def __call__(self, *args, **kwargs):
        # type: (Any, Any) -> Optional[Any]
        raise RuntimeError("Cannot call safe object")

    def __getattribute__(self, name):
        # type: (str) -> Any
        if name == "__wrapped__":
            raise AttributeError("Access denied")

        return super(SafeObjectProxy, self).__getattribute__(name)

    def __getattr__(self, name):
        # type: (str) -> Any
        return type(self).safe(super(SafeObjectProxy, self).__getattr__(name))

    @classmethod
    def safe(cls, obj):
        # type: (Any) -> Optional[Any]
        """Turn an object into a safe proxy."""
        if _isinstance(obj, CALLABLE_TYPES):
            return cls(obj)

        if isinstance(obj, type):
            try:
                if obj.__module__ == BUILTIN:
                    # We are assuming that builtin types are safe
                    return obj
            except AttributeError:
                # No __module__ attribute. We'll use caution
                pass

            return cls(obj)

        try:
            if type(obj).__module__ == BUILTIN:
                # We are assuming that builtin types are safe
                return obj
        except AttributeError:
            # No __module__ attribute. We'll use caution
            pass

        try:
            return cls(AttrDict(object.__getattribute__(obj, "__dict__")))
        except AttributeError:
            pass

        slots = get_slots(obj)
        if slots:
            # Handle slots objects
            return cls(AttrDict({k: object.__getattribute__(obj, k) for k in slots}))

        raise TypeError("Unhandled object type: %s", type(obj))
