from typing import Callable
from typing import Mapping
from typing import Optional
from typing import Type
from typing import Union

import attr

from ddtrace import _hooks
from ddtrace import config
from ddtrace import Span
from ddtrace.internal import compat


_HOOKS = _hooks.Hooks()


@attr.s(frozen=True, slots=True)
class IntegrationEvent(object):
    """
    An IntegrationEvent is emitted by an integration (e.g. the flask framework integration)
    and is linked to a span.
    """
    span = attr.ib(type=Span)
    integration = attr.ib(type=str)

    def emit(self):
        """Alias for emitting this event."""
        emit(self)

    @classmethod
    def register(cls, func=None, integration=None):
        """Alias for registering a listener for this event type."""
        return register(cls, func=func, integration=integration)

    @classmethod
    def deregister(cls, func, integration=None):
        """Alias for deregistering a listener for this event type."""
        deregister(cls, func, integration=integration)

    if config._raise:
        @span.validator
        def check_span(self, attribute, value):
            assert isinstance(value, Span)

        @integration.validator
        def check_integration(self, attribute, value):
            assert value in config._config


@attr.s(frozen=True, slots=True)
class WebRequest(IntegrationEvent):
    """
    The WebRequest event is emitted by web framework integrations before the WebResponse event.
    """
    method = attr.ib(type=str)
    url = attr.ib(type=str)
    headers = attr.ib(type=Mapping[str, str], factory=dict)
    query = attr.ib(type=Optional[str], default=None)

    if config._raise:
        @method.validator
        def check_method(self, attribute, value):
            assert value in ("GET", "POST", "PUT", "PATCH", "DELETE")

        @url.validator
        def check_url(self, attribute, value):
            compat.parse.urlparse(value)


@attr.s(frozen=True, slots=True)
class WebResponse(IntegrationEvent):
    """
    The WebResponse event is emitted by web frameworks after the WebRequest event.
    """
    status_code = attr.ib(type=Union[int, str])
    status_msg = attr.ib(type=Optional[str], default=None)
    headers = attr.ib(type=Mapping[str, str], factory=dict)

    if config._raise:
        @status_code.validator
        def check_status_code(self, attribute, value):
            int(value)


def emit(event):
    # type: (IntegrationEvent) -> None
    """
    Notify registered listeners about an event.

    Integration-specific listeners are notified before global listeners.
    """
    # Integration-specific hook listeners
    _HOOKS.emit((event.integration, event.__class__), event)
    # Global hook listeners
    _HOOKS.emit((None, event.__class__), event)


def register(event_type, func=None, integration=None):
    # type: (Type[IntegrationEvent], Optional[Callable], Optional[str]) -> Optional[Callable]
    """
    Register a function for a specific event type and optionally for events coming exclusively from a given integration.
    """
    return _HOOKS.register((integration, event_type), func)


def deregister(event_type, func, integration=None):
    # type: (Type[IntegrationEvent], Callable, Optional[str]) -> None
    """
    Deregister a function for an event type and integration.
    """
    _HOOKS.deregister((integration, event_type), func)
