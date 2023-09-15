"""
This file implements the Core API, the abstraction layer between Integrations and Product code.
The Core API enables two primary use cases: maintaining a tree of ``ExecutionContext`` objects
and dispatching events.

When using the Core API, keep concerns separate between Products and Integrations. Integrations
should not contain any code that references Products (Tracing, AppSec, Spans, WAF, Debugging, et cetera)
and Product code should never reference the library being integrated with (for example by importing ``flask``).

It's helpful to think of the context tree as a Trace with extra data on each Span. It's similar
to a tree of Spans in that it represents the parts of the execution state that Datadog products
care about.

This example shows how ``core.context_with_data`` might be used to create a node in this context tree::


    import flask


    def _patched_request(pin, wrapped, args, kwargs):
        with core.context_with_data(
            "flask._patched_request",
            pin=pin,
            flask_request=flask.request,
            block_request_callable=_block_request_callable,
        ) as ctx, ctx.get_item("flask_request_call"):
            return wrapped(*args, **kwargs)


This example looks a bit like a span created by ``tracer.trace()``: it has a name, a ``Pin`` instance, and
``__enter__`` and ``__exit__`` functionality as a context manager. In fact, it's so similar to a span that
the Tracing code in ``ddtrace/tracing`` can create a span directly from it (that's what ``flask_request_call``
is in this example).

The ``ExecutionContext`` object in this example also holds some data that you wouldn't typically find on
spans, like ``flask_request`` and ``block_request_callable``. These illustrate the context's utility as a
generic container for data that Datadog products need related to the current execution. ``block_request_callable``
happens to be used in ``ddtrace/appsec`` by the AppSec product code to make request-blocking decisions, and
``flask_request`` is a reference to a library-specific function that Tracing uses.

The first argument to ``context_with_data`` is the unique name of the context. When choosing this name,
consider how to differentiate it from other similar contexts while making its purpose clear. An easy default
is to use the name of the function within which ``context_with_data`` is being called, prefixed with the
integration name and a dot, for example ``flask._patched_request``.

The integration code finds all of the library-specific objects that products need, and puts them into
the context tree it's building via ``context_with_data``. Product code then accesses the data it needs
by calling ``ExecutionContext.get_item`` like this::


    pin = ctx.get_item("pin")
    current_span = pin.tracer.current_span()
    ctx.set_item("current_span", current_span)
    flask_config = ctx.get_item("flask_config")
    _set_request_tags(ctx.get_item("flask_request"), current_span, flask_config)


Integration code can also call ``get_item`` when necessary, for example when the Flask integration checks
the request blocking flag that may have been set on the context by AppSec code and then runs Flask-specific
response logic::


    if core.get_item(HTTP_REQUEST_BLOCKED):
        result = start_response("403", [("content-type", "application/json")])


In order for ``get_item`` calls in Product code like ``ddtrace/appsec`` to find what they're looking for,
they need to happen at the right time. That's the problem that the ``core.dispatch`` and ``core.on``
functions solve.

The common pattern is that integration code generates events by calling ``dispatch`` and product code
listens to those events by calling ``on``. This allows product code to react to integration code at the
appropriate moments while maintaining clear separation of concerns.

For example, the Flask integration calls ``dispatch`` to indicate that a blocked response just started,
passing some data along with the event::


    call = tracer.trace("operation")
    core.dispatch("flask.blocked_request_callable", call)


The AppSec code listens for this event and does some AppSec-specific stuff in the handler::


    def _on_flask_blocked_request():
        core.set_item(HTTP_REQUEST_BLOCKED, True)
    core.on("flask.blocked_request_callable", _on_flask_blocked_request)


``ExecutionContexts`` also generate their own start and end events that Product code can respond to
like this::


    def _on_jsonify_context_started_flask(ctx):
        span = ctx.get_item("pin").tracer.trace(ctx.get_item("name"))
        ctx.set_item("flask_jsonify_call", span)
    core.on("context.started.flask.jsonify", _on_jsonify_context_started_flask)


The names of these events follow the pattern ``context.[started|ended].<context_name>``.
"""
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
        results = []
        exceptions = []
        for listener in self._listeners.get(event_id, []):
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
