from functools import lru_cache
from functools import wraps
from inspect import FullArgSpec
from inspect import getfullargspec
from inspect import isgeneratorfunction
from typing import Any  # noqa:F401
from typing import Callable  # noqa:F401
from typing import Generic
from typing import Optional  # noqa:F401
from typing import TypeVar
import weakref


miss = object()

T = TypeVar("T")
F = Callable[[T], Any]
M = Callable[[Any, T], Any]

WK = TypeVar("WK")
WV = TypeVar("WV")


def cached(maxsize: int = 256) -> Callable[[Callable], Callable]:
    def _(f: Callable) -> Callable:
        return lru_cache(maxsize)(f)

    return _


class CachedMethodDescriptor:
    def __init__(self, method: M, maxsize: int) -> None:
        self._method = method
        self._maxsize = maxsize

    def __get__(self, obj: Any, objtype: Optional[type] = None) -> F:
        cached_method = cached(self._maxsize)(self._method.__get__(obj, objtype))
        setattr(obj, self._method.__name__, cached_method)
        return cached_method


def cachedmethod(maxsize: int = 256) -> Callable[[M], CachedMethodDescriptor]:
    """Decorator for memoizing methods of a single argument (LFU policy)."""

    def cached_wrapper(f: M) -> CachedMethodDescriptor:
        return CachedMethodDescriptor(f, maxsize)

    return cached_wrapper


class IdentityWeakKeyDictionary(Generic[WK, WV]):
    """Weak mapping keyed by object identity (id()), not equality or hash.

    Unlike ``weakref.WeakKeyDictionary``, insertion and lookup never call
    ``hash(key)`` or ``key.__eq__``: the internal dict is keyed on ``id(key)``,
    with an ``is`` check to detect id reuse after the original key was
    garbage collected. Use this instead of a plain ``lru_cache`` or
    ``WeakKeyDictionary`` whenever hashing the key could run arbitrary code
    (e.g. a class whose metaclass defines ``__hash__``) or raise (e.g.
    ``__hash__ = None``), or whenever the key type's ``__eq__``/``__hash__``
    would conflate objects that must stay distinct (e.g. two structurally
    identical but separately created code objects).
    """

    __slots__ = ("_data",)

    def __init__(self) -> None:
        self._data: dict[int, tuple["weakref.ref[WK]", WV]] = {}

    def _make_remove(self, key_id: int) -> Callable[["weakref.ref[WK]"], None]:
        def remove(_ref: "weakref.ref[WK]") -> None:
            self._data.pop(key_id, None)

        return remove

    def get(self, key: WK, default: Any = None) -> Any:
        item = self._data.get(id(key))
        if item is None:
            return default
        ref, value = item
        if ref() is key:
            return value
        return default

    def __contains__(self, key: WK) -> bool:
        item = self._data.get(id(key))
        return item is not None and item[0]() is key

    def __getitem__(self, key: WK) -> WV:
        item = self._data.get(id(key))
        if item is None or item[0]() is not key:
            raise KeyError(key)
        return item[1]

    def __setitem__(self, key: WK, value: WV) -> None:
        key_id = id(key)
        self._data[key_id] = (weakref.ref(key, self._make_remove(key_id)), value)

    def __delitem__(self, key: WK) -> None:
        key_id = id(key)
        if key_id not in self._data:
            raise KeyError(key)
        del self._data[key_id]

    def pop(self, key: WK, *default: WV) -> WV:
        try:
            value = self[key]
        except KeyError:
            if default:
                return default[0]
            raise
        del self[key]
        return value


def is_not_void_function(f, argspec: FullArgSpec):
    return (
        argspec.args
        or argspec.varargs
        or argspec.varkw
        or argspec.defaults
        or argspec.kwonlyargs
        or argspec.kwonlydefaults
        or isgeneratorfunction(f)
    )


def callonce(f: Callable[[], Any]) -> Callable[[], Any]:
    """Decorator for executing a function only the first time."""
    argspec = getfullargspec(f)
    if is_not_void_function(f, argspec):
        raise ValueError("The callonce decorator can only be applied to functions with no arguments")

    @wraps(f)
    def _() -> Any:
        try:
            retval, exc = f.__callonce_result__  # type: ignore[attr-defined]
        except AttributeError:
            try:
                retval = f()
                exc = None
            except Exception as e:
                retval = None
                exc = e
            f.__callonce_result__ = retval, exc  # type: ignore[attr-defined]

        if exc is not None:
            raise exc

        return retval

    return _
