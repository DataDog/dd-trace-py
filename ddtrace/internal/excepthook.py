"""Library-wide `sys.excepthook` management.

ddtrace has multiple components (telemetry, crashtracking) that want to be
notified when an unhandled exception reaches `sys.excepthook`. Rather than each
component overwriting `sys.excepthook` and chaining to the "previous" hook
which is fragile with respect to install/uninstall ordering - this module owns a
single dispatching hook that ddtrace installs at most once.

Components call `register` / `unregister`. The dispatcher runs every
registered callback (each guarded so a failing callback cannot break the others
or the traceback), then calls whatever hook existed before ddtrace installed the
dispatcher, so the default traceback is still printed and any pre-ddtrace user
hook still runs.

Registered callbacks must NOT call `sys.excepthook` themselves; the dispatcher
owns the chain.
"""

import sys
import threading
from types import TracebackType
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

ExceptHookType = Callable[[type[BaseException], BaseException, Optional[TracebackType]], Any]

_lock = threading.Lock()
_hooks: list[ExceptHookType] = []
# The hook that was installed before ddtrace took over sys.excepthook. Captured
# once, the first time a callback is registered. Always run last.
_previous_excepthook: Optional[ExceptHookType] = None
_installed = False


def _ddtrace_excepthook(
    exc_type: type[BaseException], exc_value: BaseException, exc_traceback: Optional[TracebackType]
) -> None:
    for hook in list[ExceptHookType](_hooks):
        try:
            hook(exc_type, exc_value, exc_traceback)
        except Exception:
            log.debug("Error running excepthook %r", hook, exc_info=True)

    previous = _previous_excepthook
    if previous is not None:
        previous(exc_type, exc_value, exc_traceback)


def _install_locked() -> None:
    global _installed, _previous_excepthook
    if _installed:
        return
    # Capture whatever hook is currently installed (a user-provided hook or the
    # interpreter default) so it still runs after ddtrace's callbacks. Only
    # captured once, so re-registration cannot lose it or capture our own hook.
    _previous_excepthook = sys.excepthook
    sys.excepthook = _ddtrace_excepthook
    _installed = True


def register(hook: ExceptHookType) -> None:
    """Register a callback to run when an unhandled exception reaches `sys.excepthook`.

    Installs ddtrace's dispatching `sys.excepthook` the first time it is called.
    Callbacks run in registration order, before the hook that existed prior to
    ddtrace. Registering the same callback twice is a no-op.
    """
    with _lock:
        _install_locked()
        if hook not in _hooks:
            _hooks.append(hook)


def unregister(hook: ExceptHookType) -> None:
    """Remove a previously registered callback. No-op if it is not registered."""
    with _lock:
        try:
            _hooks.remove(hook)
        except ValueError:
            pass
