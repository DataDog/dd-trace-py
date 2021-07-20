from typing import Callable
from typing import Mapping
from typing import Optional
from typing import Type
from typing import Union

import attr

from ddtrace._hooks import Hooks
from ddtrace.span import Span


_HOOKS = Hooks()


@attr.s(frozen=True, slots=True)
class IntegrationEvent(object):
    span = attr.ib(type=Span)
    integration = attr.ib(type=str)

    def emit(self):
        """Shotcurt for emitting this event."""
        emit(self)

    @classmethod
    def register(cls, func=None, integration=None):
        """Shotcurt for registering a listener for this event."""
        return register(cls, func=func, integration=integration)

    @classmethod
    def deregister(cls, func, integration=None):
        """Shotcurt for deregistering a listener for this event."""
        deregister(cls, func, integration=integration)


@attr.s(frozen=True, slots=True)
class HTTPRequest(IntegrationEvent):
    """
    Notify registered listeners about an event received
    by an instrumented framework. It is emitted before the HTTP
    response event.
    """
    method = attr.ib(type=str)
    url = attr.ib(type=str)
    headers = attr.ib(type=Mapping[str, str], factory=dict)
    query = attr.ib(type=Optional[str], default=None)


@attr.s(frozen=True, slots=True)
class HTTPResponse(IntegrationEvent):
    status_code = attr.ib(type=Union[int, str])
    status_msg = attr.ib(type=Optional[str], default=None)
    headers = attr.ib(type=Mapping[str, str], factory=dict)


def emit(event):
    # type: (IntegrationEvent) -> None
    """
    Integration-specific listeners are notified before global listeners.
    """
    # Integration-specific hook listeners
    _HOOKS.emit((event.integration, event.__class__), event)
    # Global hook listeners
    _HOOKS.emit((None, event.__class__), event)


def register(event_type, func=None, integration=None):
    # type: (Type[IntegrationEvent], Optional[Callable], Optional[str]) -> Optional[Callable]
    """
    """
    return _HOOKS.register((integration, event_type), func)


def deregister(event_type, func, integration=None):
    # type: (Type[IntegrationEvent], Callable, Optional[str]) -> None
    """
    """
    _HOOKS.deregister((integration, event_type), func)
