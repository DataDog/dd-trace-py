from typing import Any
from typing import Callable
from typing import Optional

from ddtrace.settings import config


_listeners: dict[str, list[Callable[..., Any]]] = {}


def has_listeners(event_id: str) -> bool:
    global _listeners
    return bool(_listeners.get(event_id))


def on(event_id: str, callback: Callable[..., Any]) -> None:
    global _listeners
    if event_id not in _listeners:
        _listeners[event_id] = [callback]
    elif callback not in _listeners[event_id]:
        _listeners[event_id].insert(0, callback)


def reset() -> None:
    global _listeners
    _listeners.clear()


def dispatch(event_id: str, args: tuple[Any, ...]) -> None:
    global _listeners
    if event_id not in _listeners:
        return

    for hook in _listeners[event_id]:
        try:
            hook(*args)
        except Exception:
            if config._raise:
                raise


def dispatch_with_results(event_id: str, args: tuple[Any, ...]) -> tuple[list[Any], list[Optional[Exception]]]:
    global _listeners
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
