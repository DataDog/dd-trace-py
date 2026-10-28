"""Single owner of the bootstrap profiler, ddtrace.profiling.bootstrap.profiler.

Code that starts or stops that profiler must hold lock while it builds the profiler, and swaps it.
"""

import importlib
import sys
import typing as t

from ddtrace.internal import forksafe


if t.TYPE_CHECKING:
    from ddtrace.profiling.profiler import Profiler


# Importing ddtrace.profiling loads the whole profiler and installs its hooks, which every process that loads the
# profiling product would pay for, even with profiling disabled. A profiler can only run once ddtrace.profiling was
# imported, so this module looks for it in sys.modules instead of importing it.
_BOOTSTRAP_MODULE: t.Final[str] = "ddtrace.profiling.bootstrap"

# A fork can happen while a thread holds the lock, e.g. the main thread in start_profiler when uWSGI forks workers in
# C without running the before-fork hooks. That thread does not exist in the child, so the
# lock must be reset there.
# TODO: Remote Config will also start and stop the profiler from its own thread, and will need this lock.
lock = forksafe.Lock()


def current_profiler() -> t.Optional["Profiler"]:
    # Note: this does not detect user-created Profiler instances, only the ones created
    # either by bootstrap or by remote configuration.
    return getattr(sys.modules.get(_BOOTSTRAP_MODULE), "profiler", None)


def stop_profiler() -> None:
    """Stop the bootstrap profiler and remove it. The caller must hold the Lifecycle Lock."""
    current = current_profiler()
    if current is None:
        return

    # If this raises, delattr will not be called. This is on purpose, as it would be a
    # very weird state to have to worry about in case another profiler is started later.
    current.stop(flush=True)
    delattr(sys.modules[_BOOTSTRAP_MODULE], "profiler")


def set_profiler(profiler: "Profiler") -> None:
    """Make profiler the bootstrap profiler.

    The caller must hold the Lifecycle Lock, and must have stopped the previous Profiler.
    """
    setattr(importlib.import_module(_BOOTSTRAP_MODULE), "profiler", profiler)
