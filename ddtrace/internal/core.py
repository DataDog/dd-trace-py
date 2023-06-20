from collections import defaultdict
from contextlib import contextmanager
import logging
import threading
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple


try:
    import contextvars
except ImportError:
    import ddtrace.vendor.contextvars as contextvars  # type: ignore


log = logging.getLogger(__name__)
_CONTEXT_DATA = contextvars.ContextVar("ExecutionContext_var", default=dict())


class ExecutionContext:
    def __init__(self, identifier: str, parent=None, **kwargs):
        self.identifier = identifier
        self._parents = []
        self._children = []
        if parent is not None:
            self.addParent(parent)
        # self._data = _CONTEXT_DATA.get()
        self._data = dict(**kwargs)

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
        return dispatch("context.ended.%s" % self.identifier, [])

    def addParent(self, context):
        if self is root_context:
            raise ValueError("Cannot add parent to root context")
        self._parents.append(context)

    def addChild(self, context):
        self._children.append(context)

    @classmethod
    @contextmanager
    def context_with_data(cls, identifier, parent=None, **kwargs):
        global current_context
        new_context = cls(identifier, parent=parent, **kwargs)
        prior_context = current_context
        current_context = new_context
        try:
            yield new_context
        finally:
            new_context.end()
            current_context = prior_context

    def get_item(self, data_key: str) -> Optional[Any]:
        return self._data.get(data_key)

    def set_item(self, data_key: str, data_value: Optional[Any]):
        self._data[data_key] = data_value


root_context = ExecutionContext("root")
current_context = root_context


def context_with_data(identifier: str, parent=None, **kwargs):
    return ExecutionContext.context_with_data(identifier, parent=(parent or current_context), **kwargs)


def get_item(data_key):
    return current_context.get_item(data_key)


class EventHub:
    def __init__(self):
        self._listeners = defaultdict(list)
        self._dispatch_lock = threading.Lock()

    def has_listeners(self, event_id):
        return event_id in self._listeners

    def on(self, event_id, callback):
        with self._dispatch_lock:
            self._listeners[event_id].append(callback)

    def dispatch(self, event_id: str, args: List[Any]):
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


_event_hub = EventHub()


def has_listeners(event_id):
    return _event_hub.has_listeners(event_id)


def on(event_id: str, callback: Callable):
    return _event_hub.on(event_id, callback)


def dispatch(event_id: str, args: List[Any]) -> Tuple[List[Optional[Any]], List[Optional[Exception]]]:
    return _event_hub.dispatch(event_id, args)
