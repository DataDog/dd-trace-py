from collections import defaultdict
from contextlib import contextmanager
import logging
import threading
from typing import TYPE_CHECKING


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Callable
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
ROOT_CONTEXT_ID = "__root"


class ExecutionContext:
    def __init__(self, identifier, parent=None, **kwargs):
        # type: (str, Optional[ExecutionContext], ...) -> ExecutionContext
        self.identifier = identifier
        self._data = dict()
        self._parents = []
        self._children = []
        self._event_hub = EventHub()
        if parent is not None:
            self.addParent(parent)
        self._data.update(kwargs)
        if _CURRENT_CONTEXT is not None:
            self._token = _CURRENT_CONTEXT.set(self)

    def __repr__(self):
        return "ExecutionContext '" + self.identifier + "'"

    @property
    def parents(self):
        return self._parents

    @property
    def parent(self):
        return self._parents[0] if self._parents else None

    @property
    def children(self):
        return self._children

    def end(self):
        _CURRENT_CONTEXT.reset(self._token)
        return dispatch("context.ended.%s" % self.identifier, [])

    def addParent(self, context):
        if self.identifier == ROOT_CONTEXT_ID:
            raise ValueError("Cannot add parent to root context")
        self._parents.append(context)
        self._data.update(context._data)

    def addChild(self, context):
        self._children.append(context)

    @classmethod
    @contextmanager
    def context_with_data(cls, identifier, parent=None, **kwargs):
        new_context = cls(identifier, parent=parent, **kwargs)
        try:
            yield new_context
        finally:
            new_context.end()

    def get_item(self, data_key):
        # type: (str) -> Optional[Any]
        # NB mimic the behavior of `ddtrace.internal._context` by doing lazy inheritance
        current = self
        while current is not None and current.identifier != ROOT_CONTEXT_ID:
            if data_key in current._data:
                return current._data.get(data_key)
            current = current.parent

    def get_items(self, data_keys):
        # type: (List[str]) -> Optional[Any]
        return [self.get_item(key) for key in data_keys]

    def set_item(self, data_key, data_value):
        # type: (str, Optional[Any])
        self._data[data_key] = data_value

    def set_items(self, keys_values):
        # type: (List[Tuple[str, Optional[Any]]])
        for data_key, data_value in keys_values:
            self.set_item(data_key, data_value)


_CURRENT_CONTEXT = contextvars.ContextVar("ExecutionContext_var", default=ExecutionContext(ROOT_CONTEXT_ID))


def context_with_data(identifier, parent=None, **kwargs):
    # type: (str, Optional[ExecutionContext], ...)
    return ExecutionContext.context_with_data(identifier, parent=(parent or _CURRENT_CONTEXT.get()), **kwargs)


def _choose_context(span=None):
    # type: (Optional[Span])
    if span:
        return span._execution_context
    else:
        return _CURRENT_CONTEXT.get()


def get_item(data_key, span=None):
    # type: (str, Optional[Span]) -> Optional[Any]
    return _choose_context(span).get_item(data_key)


def get_items(data_keys, span=None):
    # type: (List[str], Optional[Span]) -> Optional[Any]
    return _choose_context(span).get_items(data_keys)


def set_item(data_key, data_value, span=None):
    # type: (str, Optional[Any], Optional[Span])
    return _choose_context(span).set_item(data_key, data_value)


def set_items(keys_values, span=None):
    # type: (List[Tuple[str, Optional[Any]]], Optional[Span])
    return _choose_context(span).set_items(keys_values)


class EventHub:
    def __init__(self):
        self._listeners = defaultdict(list)
        self._dispatch_lock = threading.Lock()

    def has_listeners(self, event_id):
        # type: (str) -> bool
        return event_id in self._listeners

    def on(self, event_id, callback):
        # type: (str, Callable)
        with self._dispatch_lock:
            self._listeners[event_id].append(callback)

    def dispatch(self, event_id, args):
        # type: (str, List[Optional[Any]]) -> Tuple[List[Optional[Any]], List[Optional[Exception]]]
        with self._dispatch_lock:
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


def has_listeners(event_id, span=None):
    # type: (str, Optional[Span]) -> bool
    return _choose_context(span)._event_hub.has_listeners(event_id)


def on(event_id, callback, span=None):
    # type: (str, Callable, Optional[Span])
    return _choose_context(span)._event_hub.on(event_id, callback)


def dispatch(event_id, args, span=None):
    # type: (str, List[Optional[Any]], Optional[Span]) -> Tuple[List[Optional[Any]], List[Optional[Exception]]]
    return _choose_context(span)._event_hub.dispatch(event_id, args)
