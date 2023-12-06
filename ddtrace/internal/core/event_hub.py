from typing import Any
from typing import Callable
from typing import Optional

from ddtrace import config


_listeners: dict[str, list[Callable[..., Any]]] = {}
_all_listeners: list[Callable[[str, tuple[Any, ...]], None]] = []


def has_listeners(event_id: str) -> bool:
    """Check if there are hooks registered for the provided event_id"""
    global _listeners
    return bool(_listeners.get(event_id))


def on(event_id: str, callback: Callable[..., Any]) -> None:
    """Register a listener for the provided event_id"""
    global _listeners
    if event_id not in _listeners:
        _listeners[event_id] = [callback]
    elif callback not in _listeners[event_id]:
        _listeners[event_id].insert(0, callback)


def on_all(callback: Callable[..., Any]) -> None:
    """Register a listener for all events emitted"""
    global _all_listeners
    if callback not in _all_listeners:
        _all_listeners.insert(0, callback)


def reset() -> None:
    """Remove all registered listeners"""
    global _listeners
    global _all_listeners
    _listeners.clear()
    _all_listeners.clear()


def dispatch(event_id: str, args: tuple[Any, ...]) -> None:
    """Call all hooks for the provided event_id with the provided args"""
    global _all_listeners
    global _listeners

    for hook in _all_listeners:
        try:
            hook(event_id, args)
        except Exception:
            if config._raise:
                raise

    if event_id not in _listeners:
        return

    for hook in _listeners[event_id]:
        try:
            hook(*args)
        except Exception:
            if config._raise:
                raise


def dispatch_with_results(event_id: str, args: tuple[Any, ...]) -> tuple[list[Any], list[Optional[Exception]]]:
    """Call all hooks for the provided event_id with the provided args
    returning the results and exceptions from the called hooks
    """
    global _listeners
    global _all_listeners

    for hook in _all_listeners:
        try:
            hook(event_id, args)
        except Exception:
            if config._raise:
                raise

    if event_id not in _listeners:
        return [], []

    results: list[Any] = []
    exceptions: list[Optional[Exception]] = []
    for hook in _listeners[event_id]:
        try:
            results.append(hook(*args))
            exceptions.append(None)
        except Exception as e:
            if config._raise:
                raise

            results.append(None)
            exceptions.append(e)

    return results, exceptions
