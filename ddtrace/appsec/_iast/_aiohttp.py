from collections.abc import Awaitable
from collections.abc import ItemsView
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import KeysView
from collections.abc import Mapping
from collections.abc import MutableMapping
from collections.abc import ValuesView
from contextvars import ContextVar
import operator
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import ClassVar

from multidict import CIMultiDict
from multidict import CIMultiDictProxy
from multidict import MultiDict
from multidict import MultiDictProxy

from ddtrace.appsec._iast._constants import IAST
from ddtrace.appsec._iast._iast_request_context_base import is_iast_request_enabled
from ddtrace.appsec._iast._iast_request_context_base import set_iast_request_endpoint
from ddtrace.appsec._iast._logs import iast_instrumentation_wrapt_debug_log
from ddtrace.appsec._iast._logs import iast_propagation_listener_log_log
from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_source
from ddtrace.appsec._iast._patch_modules import WrapFunctonsForIAST
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import origin_to_str
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted
from ddtrace.appsec._iast._taint_utils import taint_structure
from ddtrace.contrib.internal.aiohttp.middlewares import CONFIG_KEY
from ddtrace.internal import core
from ddtrace.internal.security._taint_sources import _is_iast_taint_source_enabled


if TYPE_CHECKING:
    _MultiDictProxy = MultiDictProxy[Any]
    _CIMultiDictProxy = CIMultiDictProxy[Any]
else:
    _MultiDictProxy = MultiDictProxy
    _CIMultiDictProxy = CIMultiDictProxy

_MISSING = object()
_BODY_SOURCE = origin_to_str(OriginType.BODY)
_PATH_SOURCE = origin_to_str(OriginType.PATH)
_QUERY_SOURCE = origin_to_str(OriginType.QUERY)
_reading_body: ContextVar[bool] = ContextVar("_dd_iast_aiohttp_reading_body", default=False)
_sources_patched = False
_FORM_CACHE_KEY = "_dd_iast_form"
_SOURCES_KEY = "_dd_iast_sources"
_WRAPPED_KEY = "_dd_iast_wrapped"


def _taint_source(value: Any, name: Any, origin: OriginType) -> Any:
    # Empty values are never tainted, but tainting them would still count an executed source.
    if not value:
        return value
    try:
        return taint_pyobject(pyobject=value, source_name=name, source_value=value, source_origin=origin)
    except Exception:
        iast_propagation_listener_log_log("Unexpected exception while tainting aiohttp request data", exc_info=True)  # type: ignore[no-untyped-call]
        return value


class _TaintedOnRead:
    """Taints each name or value of a request mapping when the application first reads it."""

    _name_origin: ClassVar[OriginType]
    _value_origin: ClassVar[OriginType]
    _dd_iast_tainted: dict[tuple[OriginType, Any, Any], Any]

    def _value_origin_for(self, name: Any) -> OriginType:
        return self._value_origin

    def _cache_name(self, name: Any) -> Any:
        return name

    def _taint(self, value: Any, name: Any, origin: OriginType) -> Any:
        if not value or not isinstance(value, IAST.TAINTEABLE_TYPES) or not _is_iast_taint_source_enabled():
            return value
        key = (origin, self._cache_name(name), id(value) if isinstance(value, bytearray) else value)
        tainted = self._dd_iast_tainted.get(key)
        if tainted is None:
            tainted = _taint_source(value, name, origin)
            if tainted is value and not is_pyobject_tainted(tainted):
                return value
            self._dd_iast_tainted[key] = tainted
        return tainted

    def _taint_name(self, name: Any) -> Any:
        return self._taint(name, name, self._name_origin)

    def _tainted_value(self, name: Any, value: Any) -> Any:
        return self._taint(value, name, self._value_origin_for(name))


class _NativeSetOperations:
    """Set operations from multidict's own view, which matches header names case-insensitively."""

    _native: Any

    def _taint_element(self, element: Any) -> Any:
        return element

    def _is_request_element(self, element: Any) -> bool:
        return isinstance(element, str)

    def _apply(self, other: Any, reflected: bool, operation: Callable[[Any, Any], Any]) -> Any:
        """Taint the elements that come from the request, leaving the application's own objects untouched."""
        if not isinstance(other, Iterable):
            return NotImplemented
        if isinstance(other, _NativeSetOperations):
            return self._apply_request(other, reflected, operation)
        # One materialized operand serves both the operation and the identity check, since multidict views can build
        # new objects on each pass.
        if not isinstance(other, (set, frozenset)):
            other = list(other)
        result = operation(other, self._native) if reflected else operation(self._native, other)
        if not isinstance(result, (set, frozenset)):
            return result
        app_objects = {id(element) for element in other}
        return {
            self._taint_element(element)
            if id(element) not in app_objects and self._is_request_element(element)
            else element
            for element in result
        }

    def _apply_request(
        self, other: "_NativeSetOperations", reflected: bool, operation: Callable[[Any, Any], Any]
    ) -> Any:
        """Both operands hold request data, so each element is tainted through the mapping it belongs to."""
        native = other._native
        result = operation(native, self._native) if reflected else operation(self._native, native)
        if not isinstance(result, (set, frozenset)):
            return result
        # Exact matches decide first: header membership is case-insensitive and would claim a query's spelling.
        owners = ((self, list(self._native)), (other, list(native)))
        tainted = set()
        for element in result:
            owner = next((source for source, exact in owners if element in exact), None)
            if owner is None:
                owner = next((source for source, _ in owners if element in source._native), None)
            if owner is not None and owner._is_request_element(element):
                element = owner._taint_element(element)
            tainted.add(element)
        return tainted

    def __and__(self, other: Any) -> Any:
        return self._apply(other, False, operator.and_)

    def __rand__(self, other: Any) -> Any:
        return other & self._native

    def __sub__(self, other: Any) -> Any:
        return self._apply(other, False, operator.sub)

    def __rsub__(self, other: Any) -> Any:
        return other - self._native

    def __or__(self, other: Any) -> Any:
        return self._apply(other, False, operator.or_)

    def __ror__(self, other: Any) -> Any:
        return self._apply(other, True, operator.or_)

    def __xor__(self, other: Any) -> Any:
        return self._apply(other, False, operator.xor)

    def __rxor__(self, other: Any) -> Any:
        return self._apply(other, True, operator.xor)

    def isdisjoint(self, other: Any) -> bool:
        return bool(self._native.isdisjoint(other))

    def __eq__(self, other: object) -> bool:
        return bool(self._native == other)

    def __ne__(self, other: object) -> bool:
        return bool(self._native != other)

    def __lt__(self, other: Any) -> bool:
        return bool(self._native < other)

    def __le__(self, other: Any) -> bool:
        return bool(self._native <= other)

    def __gt__(self, other: Any) -> bool:
        return bool(self._native > other)

    def __ge__(self, other: Any) -> bool:
        return bool(self._native >= other)

    __hash__ = None  # type: ignore[assignment]


class _TaintedKeysView(_NativeSetOperations, KeysView[Any]):
    def __init__(self, source: _TaintedOnRead, keys: KeysView[Any]) -> None:
        self._source = source
        self._keys = self._native = keys

    def _taint_element(self, element: Any) -> Any:
        return self._source._taint_name(element)

    def __len__(self) -> int:
        return len(self._keys)

    def __contains__(self, key: object) -> bool:
        return key in self._keys

    def __iter__(self) -> Iterator[Any]:
        for key in self._keys:
            yield self._source._taint_name(key)

    def __reversed__(self) -> Iterator[Any]:
        keys = reversed(self._keys)  # type: ignore[call-overload]
        return (self._source._taint_name(key) for key in keys)

    def __repr__(self) -> str:
        return repr(self._keys)


class _TaintedItemsView(_NativeSetOperations, ItemsView[Any, Any]):
    def __init__(self, source: _TaintedOnRead, items: ItemsView[Any, Any]) -> None:
        self._source = source
        self._items = self._native = items

    def _taint_element(self, element: Any) -> Any:
        key, value = element
        return self._source._taint_name(key), self._source._tainted_value(key, value)

    def _is_request_element(self, element: Any) -> bool:
        return isinstance(element, tuple) and len(element) == 2 and isinstance(element[0], str)

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, item: object) -> bool:
        return item in self._items

    def __iter__(self) -> Iterator[tuple[Any, Any]]:
        for item in self._items:
            yield self._taint_element(item)

    def __reversed__(self) -> Iterator[tuple[Any, Any]]:
        items = reversed(self._items)  # type: ignore[call-overload]
        return (self._taint_element(item) for item in items)

    def __repr__(self) -> str:
        return repr(self._items)


class _TaintedValuesView(ValuesView[Any]):
    def __init__(self, source: _TaintedOnRead, values: ValuesView[Any], items: ItemsView[Any, Any]) -> None:
        self._source = source
        self._values = values
        self._items = items

    def __len__(self) -> int:
        return len(self._values)

    def __contains__(self, value: object) -> bool:
        return value in self._values

    def __iter__(self) -> Iterator[Any]:
        for key, value in self._items:
            yield self._source._tainted_value(key, value)

    def __reversed__(self) -> Iterator[Any]:
        reversed(self._values)  # type: ignore[call-overload]
        items = reversed(self._items)  # type: ignore[call-overload]
        return (self._source._tainted_value(key, value) for key, value in items)

    def __repr__(self) -> str:
        return repr(self._values)


class _TaintedMultiDictProxy(_TaintedOnRead, _MultiDictProxy):
    _copy_type: ClassVar[Callable[..., Any]] = MultiDict
    _native_name: ClassVar[str] = "MultiDictProxy"

    def __init__(self, proxy: Any) -> None:
        super().__init__(proxy)
        self._dd_iast_tainted = {}

    def __getitem__(self, key: Any) -> Any:
        return self._tainted_value(key, super().__getitem__(key))

    def get(self, key: Any, default: Any = None) -> Any:
        try:
            value = super().getone(key)
        except KeyError:
            return default
        return self._tainted_value(key, value)

    def getone(self, key: Any, default: Any = _MISSING) -> Any:
        try:
            value = super().getone(key)
        except KeyError:
            if default is _MISSING:
                raise
            return default
        return self._tainted_value(key, value)

    def getall(self, key: Any, default: Any = _MISSING) -> Any:
        try:
            values = super().getall(key)
        except KeyError:
            if default is _MISSING:
                raise
            return default
        return [self._tainted_value(key, value) for value in values]

    def __iter__(self) -> Iterator[Any]:
        return iter(self.keys())

    if getattr(_MultiDictProxy, "__reversed__", None) is None:
        # Without this, reversed() would fall back to __len__ and __getitem__ and only fail on the first item.
        __reversed__ = None
    else:

        def __reversed__(self) -> Iterator[Any]:
            return reversed(self.keys())

    def keys(self) -> Any:
        return _TaintedKeysView(self, super().keys())

    def items(self) -> Any:
        return _TaintedItemsView(self, super().items())

    def values(self) -> Any:
        return _TaintedValuesView(self, super().values(), super().items())

    def copy(self) -> Any:
        return self._copy_type(list(self.items()))

    def __repr__(self) -> str:
        return super().__repr__().replace(type(self).__name__, self._native_name, 1)


class TaintedHeaders(_TaintedMultiDictProxy, _CIMultiDictProxy):
    _name_origin = OriginType.HEADER_NAME
    _value_origin = OriginType.HEADER
    _copy_type = CIMultiDict
    _native_name = "CIMultiDictProxy"

    def _value_origin_for(self, name: Any) -> OriginType:
        return OriginType.COOKIE if name.lower() == "cookie" else OriginType.HEADER

    def _cache_name(self, name: Any) -> Any:
        return name.lower()


class TaintedQuery(_TaintedMultiDictProxy):
    _name_origin = OriginType.PARAMETER_NAME
    _value_origin = OriginType.PARAMETER


class TaintedForm(_TaintedMultiDictProxy):
    _name_origin = OriginType.PARAMETER_NAME
    _value_origin = OriginType.BODY


class _TaintedCookieKeys(KeysView[Any]):
    _mapping: "TaintedCookies"

    def __reversed__(self) -> Iterator[Any]:
        return reversed(self._mapping)


class _TaintedCookieItems(ItemsView[Any, Any]):
    _mapping: "TaintedCookies"

    def __reversed__(self) -> Iterator[tuple[Any, Any]]:
        cookies = self._mapping
        return (
            (cookies._taint_name(key), cookies._tainted_value(key, cookies._cookies[key]))
            for key in reversed(list(cookies._cookies))
        )


class _TaintedCookieValues(ValuesView[Any]):
    _mapping: "TaintedCookies"

    def __reversed__(self) -> Iterator[Any]:
        cookies = self._mapping
        return (cookies._tainted_value(key, cookies._cookies[key]) for key in reversed(list(cookies._cookies)))


class TaintedCookies(_TaintedOnRead, Mapping[str, Any]):
    _name_origin = OriginType.COOKIE_NAME
    _value_origin = OriginType.COOKIE

    def __init__(self, cookies: Mapping[str, str]) -> None:
        self._cookies = cookies
        self._dd_iast_tainted = {}

    def __getitem__(self, key: str) -> Any:
        return self._tainted_value(key, self._cookies[key])

    def __iter__(self) -> Iterator[Any]:
        for key in self._cookies:
            yield self._taint_name(key)

    def __reversed__(self) -> Iterator[Any]:
        for key in reversed(list(self._cookies)):
            yield self._taint_name(key)

    def __len__(self) -> int:
        return len(self._cookies)

    def __contains__(self, key: object) -> bool:
        return key in self._cookies

    def __repr__(self) -> str:
        return repr(self._cookies)

    def keys(self) -> Any:
        return _TaintedCookieKeys(self)

    def items(self) -> Any:
        return _TaintedCookieItems(self)

    def values(self) -> Any:
        return _TaintedCookieValues(self)

    # Like a mapping proxy, unions go through a copy, so a dict subclass operand such as defaultdict keeps its type.
    def __or__(self, other: Any) -> Any:
        return self.copy() | other

    def __ror__(self, other: Any) -> Any:
        return other | self.copy()

    def copy(self) -> dict[str, Any]:
        return dict(self.items())


def _proxies_accept_subclasses() -> bool:
    try:
        CIMultiDictProxy(TaintedHeaders(CIMultiDictProxy(CIMultiDict())))
        MultiDictProxy(TaintedQuery(MultiDictProxy(MultiDict())))
    except TypeError:
        return False
    return True


# multidict before 6.4 builds proxies only from its exact types, so wrapped mappings would break application code.
WRAPS_MAPPINGS = _proxies_accept_subclasses()


def _defers_to_inner_traced_app(request: Any) -> bool:
    """With trace_app on a parent and a sub-app, only the innermost traced app wraps the request.

    The inner middleware's tracing reads every header, which would otherwise create taint sources.
    """
    traced = [app for app in getattr(request.match_info, "apps", ()) if CONFIG_KEY in app]
    return bool(traced) and traced[-1] is not request.app


def taint_request_sources(ctx: core.ExecutionContext[Any], request: Any) -> None:
    patch_aiohttp_sources()
    # Which of the headers and URL come from the request; a clone records what its caller kept.
    headers, url = request._cache.setdefault(_SOURCES_KEY, (True, True))
    if _defers_to_inner_traced_app(request):
        return
    set_iast_request_endpoint(request.method, ctx.event.request_route)
    _wrap_request(request, headers, url)


def _wrap_request(request: Any, headers: bool, url: bool) -> None:
    cache = request._cache
    cache[_SOURCES_KEY] = (headers, url)
    cache[_WRAPPED_KEY] = True
    if headers:
        # Cookies are parsed from the headers, so they are read before the headers are replaced.
        try:
            cookies = request.cookies
        except Exception:
            # aiohttp 3.8 rejects some malformed Cookie headers, and the application gets that error if it reads them.
            cookies = None
        if cookies is not None and not isinstance(cookies, TaintedCookies):
            cache["cookies"] = TaintedCookies(cookies)
        if WRAPS_MAPPINGS and not isinstance(request.headers, TaintedHeaders):
            cache["headers"] = TaintedHeaders(request.headers)
    if url:
        if WRAPS_MAPPINGS and not isinstance(request.query, TaintedQuery):
            cache["query"] = TaintedQuery(request.query)
        path = request.path
        if not is_pyobject_tainted(path):
            cache["path"] = _taint_source(path, _PATH_SOURCE, OriginType.PATH)
        query_string = request.query_string
        if not is_pyobject_tainted(query_string):
            cache["query_string"] = _taint_source(query_string, _QUERY_SOURCE, OriginType.QUERY)
    match_info = request.match_info
    # Custom routers may return read-only match info; aiohttp's own is a dict.
    if isinstance(match_info, MutableMapping):
        for name, value in match_info.items():
            if not is_pyobject_tainted(value):
                match_info[name] = _taint_source(value, name, OriginType.PATH_PARAMETER)


def _wrap_cloned_request(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    clone = wrapped(*args, **kwargs)
    sources = instance._cache.get(_SOURCES_KEY)
    if sources is not None and is_iast_request_enabled():
        headers = sources[0] and "headers" not in kwargs
        url = sources[1] and "rel_url" not in kwargs
        try:
            if instance._cache.get(_WRAPPED_KEY):
                _wrap_request(clone, headers, url)
            else:
                # The request start of an inner traced app wraps this clone, honoring what its caller replaced.
                clone._cache[_SOURCES_KEY] = (headers, url)
        except Exception:
            iast_propagation_listener_log_log(
                "Unexpected exception while tainting cloned aiohttp request", exc_info=True
            )  # type: ignore[no-untyped-call]
    return clone


_AsyncMethod = Callable[..., Awaitable[Any]]


async def _read_body(wrapped: _AsyncMethod, args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[Any, bool]:
    """Read the body and tell whether this call is the outermost one: text(), json() and post() call read()."""
    if _reading_body.get():
        return await wrapped(*args, **kwargs), False
    token = _reading_body.set(True)
    try:
        return await wrapped(*args, **kwargs), True
    finally:
        _reading_body.reset(token)


async def _taint_body(wrapped: _AsyncMethod, instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    body, outermost = await _read_body(wrapped, args, kwargs)
    if not outermost or not is_iast_request_enabled() or not _is_iast_taint_source_enabled():
        return body
    cache_key = f"_dd_iast_{wrapped.__name__}"
    cached = instance._cache.get(cache_key)
    if cached is not None and (cached[0] is body or cached[0] == body):
        return cached[1]
    tainted = _taint_source(body, _BODY_SOURCE, OriginType.BODY)
    if tainted is not body or is_pyobject_tainted(tainted):
        instance._cache[cache_key] = (body, tainted)
    return tainted


async def _taint_json_body(wrapped: _AsyncMethod, instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    loads = kwargs.get("loads")
    if loads is not None and is_iast_request_enabled() and _is_iast_taint_source_enabled():
        # An application loader gets the body as a source; aiohttp's own nested text() read stays untainted.
        kwargs = {**kwargs, "loads": lambda text: loads(_taint_source(text, _BODY_SOURCE, OriginType.BODY))}
    body, outermost = await _read_body(wrapped, args, kwargs)
    if not outermost or not is_iast_request_enabled() or not _is_iast_taint_source_enabled():
        return body
    if isinstance(body, str):
        return _taint_source(body, _BODY_SOURCE, OriginType.BODY)
    if isinstance(body, (dict, list)):
        try:
            return taint_structure(body, OriginType.BODY, OriginType.BODY, override_pyobject_tainted=True)  # type: ignore[no-untyped-call]
        except Exception:
            iast_propagation_listener_log_log("Unexpected exception while tainting aiohttp JSON body", exc_info=True)  # type: ignore[no-untyped-call]
    return body


async def _taint_form_body(wrapped: _AsyncMethod, instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    form, outermost = await _read_body(wrapped, args, kwargs)
    if not outermost or not WRAPS_MAPPINGS or not is_iast_request_enabled() or isinstance(form, TaintedForm):
        return form
    cached = instance._cache.get(_FORM_CACHE_KEY)
    if cached is not None and cached[0] is form:
        return cached[1]
    tainted = TaintedForm(form)
    # Kept apart from the form aiohttp stores, which aiohttp iterates when the request ends.
    instance._cache[_FORM_CACHE_KEY] = (form, tainted)
    return tainted


def patch_aiohttp_sources() -> None:
    global _sources_patched
    if _sources_patched:
        return
    iast_funcs = WrapFunctonsForIAST()
    iast_funcs.wrap_function("aiohttp.web_request", "BaseRequest.read", _taint_body)
    iast_funcs.wrap_function("aiohttp.web_request", "BaseRequest.text", _taint_body)
    iast_funcs.wrap_function("aiohttp.web_request", "BaseRequest.json", _taint_json_body)
    iast_funcs.wrap_function("aiohttp.web_request", "BaseRequest.post", _taint_form_body)
    # Request.clone sets the path parameters after BaseRequest.clone returns, so the finished clone is wrapped here.
    iast_funcs.wrap_function("aiohttp.web_request", "Request.clone", _wrap_cloned_request)
    iast_funcs.patch()
    _sources_patched = True
    if not WRAPS_MAPPINGS:
        iast_instrumentation_wrapt_debug_log("multidict below 6.4: aiohttp header, query and form sources are disabled")  # type: ignore[no-untyped-call]
    for origin in instrumented_origins():
        _set_metric_iast_instrumented_source(origin)


def instrumented_origins() -> list[OriginType]:
    origins = [
        OriginType.COOKIE,
        OriginType.COOKIE_NAME,
        OriginType.PATH,
        OriginType.PATH_PARAMETER,
        OriginType.QUERY,
        OriginType.BODY,
    ]
    if WRAPS_MAPPINGS:
        origins += [OriginType.HEADER, OriginType.HEADER_NAME, OriginType.PARAMETER, OriginType.PARAMETER_NAME]
    return origins
