from collections import defaultdict
from contextlib import contextmanager
import logging
from typing import TYPE_CHECKING


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Callable
    from typing import Dict
    from typing import List
    from typing import Optional
    from typing import Tuple

    from ddtrace.span import Span  # noqa


try:
    import contextvars
except ImportError:
    import ddtrace.vendor.contextvars as contextvars  # type: ignore


log = logging.getLogger(__name__)


_CURRENT_CONTEXT = None
_EVENT_HUB = None
ROOT_CONTEXT_ID = "__root"


class EventHub:
    def __init__(self):
        self.reset()

    def has_listeners(self, event_id):
        # type: (str) -> bool
        return event_id in self._listeners

    def on(self, event_id, callback):
        # type: (str, Callable) -> None
        if callback not in self._listeners[event_id]:
            self._listeners[event_id].append(callback)

    def reset(self):
        if hasattr(self, "_listeners"):
            del self._listeners
        self._listeners = defaultdict(list)

    def dispatch(self, event_id, args, *other_args):
        # type: (...) -> Tuple[List[Optional[Any]], List[Optional[Exception]]]
        if not isinstance(args, list):
            args = [args] + list(other_args)
        else:
            if other_args:
                raise TypeError(
                    "When the first argument expected by the event handler is a list, all arguments "
                    "must be passed in a list. For example, use dispatch('foo', [[l1, l2], arg2]) "
                    "instead of dispatch('foo', [l1, l2], arg2)."
                )
        log.debug("Dispatching event %s", event_id)
        results = []
        exceptions = []
        for listener in self._listeners.get(event_id, []):
            log.debug("Calling listener %s", listener)
            result = None
            exception = None
            try:
                result = listener(*args)
            except Exception as exc:
                exception = exc
            results.append(result)
            exceptions.append(exception)
        return results, exceptions


_EVENT_HUB = contextvars.ContextVar("EventHub_var", default=EventHub())


def has_listeners(event_id):
    # type: (str) -> bool
    return _EVENT_HUB.get().has_listeners(event_id)  # type: ignore


def on(event_id, callback):
    # type: (str, Callable) -> None
    return _EVENT_HUB.get().on(event_id, callback)  # type: ignore


def reset_listeners():
    # type: () -> None
    _EVENT_HUB.get().reset()  # type: ignore


def dispatch(event_id, args, *other_args):
    # type: (...) -> Tuple[List[Optional[Any]], List[Optional[Exception]]]
    return _EVENT_HUB.get().dispatch(event_id, args, *other_args)  # type: ignore


class ExecutionContext:
    __slots__ = ["identifier", "_data", "_parents", "_span", "_token"]

    def __init__(self, identifier, parent=None, span=None, **kwargs):
        self.identifier = identifier
        self._data = {}
        self._parents = []
        self._span = span
        if parent is not None:
            self.addParent(parent)
        self._data.update(kwargs)
        if self._span is None and _CURRENT_CONTEXT is not None:
            self._token = _CURRENT_CONTEXT.set(self)
        dispatch("context.started.%s" % self.identifier, [self])

    def __repr__(self):
        return self.__class__.__name__ + " '" + self.identifier + "' @ " + str(id(self))

    @property
    def parents(self):
        return self._parents

    @property
    def parent(self):
        return self._parents[0] if self._parents else None

    def end(self):
        dispatch_result = dispatch("context.ended.%s" % self.identifier, [self])
        if self._span is None:
            try:
                _CURRENT_CONTEXT.reset(self._token)
            except ValueError:
                log.debug(
                    "Encountered ValueError during core contextvar reset() call. "
                    "This can happen when a span holding an executioncontext is "
                    "finished in a Context other than the one that started it."
                )
            except LookupError:
                log.debug(
                    "Encountered LookupError during core contextvar reset() call. I don't know why this is possible."
                )
        return dispatch_result

    def addParent(self, context):
        if self.identifier == ROOT_CONTEXT_ID:
            raise ValueError("Cannot add parent to root context")
        self._parents.append(context)

    @classmethod
    @contextmanager
    def context_with_data(cls, identifier, parent=None, span=None, **kwargs):
        new_context = cls(identifier, parent=parent, span=span, **kwargs)
        try:
            yield new_context
        finally:
            new_context.end()

    def get_item(self, data_key):
        # type: (str) -> Optional[Any]
        # NB mimic the behavior of `ddtrace.internal._context` by doing lazy inheritance
        current = self
        while current is not None:
            log.debug("Checking context '%s' for data at key '%s'", current.identifier, data_key)
            if data_key in current._data:
                return current._data.get(data_key)
            current = current.parent
        return None

    def get_items(self, data_keys):
        # type: (List[str]) -> Optional[Any]
        return [self.get_item(key) for key in data_keys]

    def set_item(self, data_key, data_value):
        # type: (str, Optional[Any]) -> None
        self._data[data_key] = data_value

    def set_safe(self, data_key, data_value):
        # type: (str, Optional[Any]) -> None
        if data_key in self._data:
            raise ValueError("Cannot overwrite ExecutionContext data key '%s'", data_key)
        return self.set_item(data_key, data_value)

    def set_items(self, keys_values):
        # type: (Dict[str, Optional[Any]]) -> None
        for data_key, data_value in keys_values.items():
            self.set_item(data_key, data_value)

    def root(self):
        if self.identifier == ROOT_CONTEXT_ID:
            return self
        current = self
        while current.parent is not None:
            current = current.parent
        return current


def __getattr__(name):
    if name == "root":
        return _CURRENT_CONTEXT.get().root()
    raise AttributeError


_CURRENT_CONTEXT = contextvars.ContextVar("ExecutionContext_var", default=ExecutionContext(ROOT_CONTEXT_ID))
_CONTEXT_CLASS = ExecutionContext


def context_with_data(identifier, parent=None, **kwargs):
    return _CONTEXT_CLASS.context_with_data(identifier, parent=(parent or _CURRENT_CONTEXT.get()), **kwargs)


def get_item(data_key, span=None):
    # type: (str, Optional[Span]) -> Optional[Any]
    if span is not None and span._local_root is not None:
        return span._local_root._get_ctx_item(data_key)
    else:
        return _CURRENT_CONTEXT.get().get_item(data_key)  # type: ignore


def get_items(data_keys, span=None):
    # type: (List[str], Optional[Span]) -> Optional[Any]
    if span is not None and span._local_root is not None:
        return [span._local_root._get_ctx_item(key) for key in data_keys]
    else:
        return _CURRENT_CONTEXT.get().get_items(data_keys)  # type: ignore


def set_safe(data_key, data_value):
    # type: (str, Optional[Any]) -> None
    _CURRENT_CONTEXT.get().set_safe(data_key, data_value)  # type: ignore


# NB Don't call these set_* functions from `ddtrace.contrib`, only from product code!
def set_item(data_key, data_value, span=None):
    # type: (str, Optional[Any], Optional[Span]) -> None
    if span is not None and span._local_root is not None:
        span._local_root._set_ctx_item(data_key, data_value)
    else:
        _CURRENT_CONTEXT.get().set_item(data_key, data_value)  # type: ignore


def set_items(keys_values, span=None):
    # type: (Dict[str, Optional[Any]], Optional[Span]) -> None
    if span is not None and span._local_root is not None:
        span._local_root._set_ctx_items(keys_values)
    else:
        _CURRENT_CONTEXT.get().set_items(keys_values)  # type: ignore
