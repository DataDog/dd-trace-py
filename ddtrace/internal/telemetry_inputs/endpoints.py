"""Whether telemetry collects the application's API endpoints, and how many it sends in a message."""

from typing import Callable
from typing import Optional


DEFAULT_LIMIT = 300


def _enabled() -> bool:
    return True


def _limit() -> int:
    return DEFAULT_LIMIT


_is_enabled: Optional[Callable[[], bool]] = None
_get_limit: Optional[Callable[[], int]] = None


def register_endpoint_collection(is_enabled: Callable[[], bool], get_limit: Callable[[], int]) -> None:
    """Set where telemetry reads the endpoint collection settings from. The first registration wins.

    Both are called on each read, so changes at runtime apply, and they can import their settings lazily.
    Without a registration, endpoints are collected and a message holds at most ``DEFAULT_LIMIT`` of them.
    """
    global _is_enabled, _get_limit

    if _is_enabled is None:
        _is_enabled = is_enabled
        _get_limit = get_limit


def endpoint_collection_enabled() -> bool:
    """Return whether endpoints are collected. True if nothing registered."""
    return (_is_enabled or _enabled)()


def endpoint_collection_limit() -> int:
    """Return the most endpoints to send in a message. ``DEFAULT_LIMIT`` if nothing registered."""
    return (_get_limit or _limit)()
