import os
import typing as t
import uuid

from .. import forksafe


__all__ = [
    "get_runtime_id",
    "get_ancestor_runtime_id",
    "get_parent_runtime_id",
]


def _generate_runtime_id() -> str:
    return uuid.uuid4().hex


# Store the PID when this module is first imported
# This allows us to detect when we're in a forked process
_INITIAL_PID: int = os.getpid()

# Module-level cache for runtime IDs
# These are initialized lazily and can change after fork
_RUNTIME_ID: t.Optional[str] = None
_ANCESTOR_RUNTIME_ID: t.Optional[str] = None
_PARENT_RUNTIME_ID: t.Optional[str] = None
_ON_RUNTIME_ID_CHANGE: t.Set[t.Callable[[str], None]] = set()


def _initialize_from_env() -> None:
    """Initialize parent/ancestor IDs from environment variables if present.

    This is called once when runtime ID is first accessed, allowing spawned
    processes to inherit lineage from their parent.
    """
    global _PARENT_RUNTIME_ID, _ANCESTOR_RUNTIME_ID

    if _PARENT_RUNTIME_ID is None:
        _PARENT_RUNTIME_ID = os.environ.get("_DD_PY_PARENT_RUNTIME_ID")
    if _ANCESTOR_RUNTIME_ID is None:
        _ANCESTOR_RUNTIME_ID = os.environ.get("_DD_PY_ANCESTOR_RUNTIME_ID")


def on_runtime_id_change(cb: t.Callable[[str], None]) -> None:
    """Register a callback to be called when the runtime ID changes.

    This can happen after a fork.
    """
    global _ON_RUNTIME_ID_CHANGE
    _ON_RUNTIME_ID_CHANGE.add(cb)


@forksafe.register
def _set_runtime_id():
    """Called after fork to regenerate runtime ID for the new process.

    This is only called on fork, not in subinterpreters.
    Propagates parent and ancestor IDs via environment variables.
    """
    global _RUNTIME_ID, _ANCESTOR_RUNTIME_ID, _PARENT_RUNTIME_ID, _INITIAL_PID

    # Save the parent runtime ID before generating a new one
    _PARENT_RUNTIME_ID = _RUNTIME_ID

    # Save the runtime ID of the common ancestor of all processes.
    if _ANCESTOR_RUNTIME_ID is None:
        _ANCESTOR_RUNTIME_ID = _RUNTIME_ID

    # Propagate parent and ancestor IDs via environment variables
    # so they're available to spawned processes
    if _PARENT_RUNTIME_ID:
        os.environ["_DD_PY_PARENT_RUNTIME_ID"] = _PARENT_RUNTIME_ID
    if _ANCESTOR_RUNTIME_ID:
        os.environ["_DD_PY_ANCESTOR_RUNTIME_ID"] = _ANCESTOR_RUNTIME_ID

    # Generate new runtime ID for the forked process
    _RUNTIME_ID = _generate_runtime_id()
    # Update the initial PID for this forked process
    _INITIAL_PID = os.getpid()

    for cb in _ON_RUNTIME_ID_CHANGE:
        cb(_RUNTIME_ID)


def get_runtime_id():
    """Return a unique string identifier for this runtime.

    The runtime ID is stable within a process but changes when the process forks.
    Subinterpreters within the same process will each have their own runtime ID.

    Note: Subinterpreters are independent runtimes with no parent/child relationship,
    even if one subinterpreter creates another. Each subinterpreter is treated as
    its own root with ancestor_id equal to its own runtime_id.

    Do not store this identifier as it can change when, e.g., the process forks.
    """
    global _RUNTIME_ID, _INITIAL_PID

    # Initialize on first call in this interpreter
    if _RUNTIME_ID is None:
        # Load parent/ancestor from env vars if we're a spawned child
        _initialize_from_env()
        _RUNTIME_ID = _generate_runtime_id()

    # Detect if we're in a forked process that the forksafe mechanism missed
    # This can happen in edge cases
    current_pid = os.getpid()
    if current_pid != _INITIAL_PID:
        # We're in a forked process but forksafe didn't run
        # Manually trigger the fork handler
        _set_runtime_id()

    return _RUNTIME_ID


def get_ancestor_runtime_id() -> str:
    """Return the runtime ID of the common ancestor of this process.

    For the root process, this returns its own runtime ID.
    For forked/spawned child processes, this returns the runtime ID of the root ancestor.
    For subinterpreters, this returns their own runtime ID (subinterpreters are independent
    with no parent/child relationships, even when nested).
    """
    global _ANCESTOR_RUNTIME_ID, _RUNTIME_ID
    # Ensure runtime ID is initialized (which also sets ancestor)
    if _ANCESTOR_RUNTIME_ID is None:
        if _RUNTIME_ID is None:
            _RUNTIME_ID = get_runtime_id()
        _ANCESTOR_RUNTIME_ID = _RUNTIME_ID
    return _ANCESTOR_RUNTIME_ID


def get_parent_runtime_id() -> t.Optional[str]:
    """Return the runtime ID of the parent process.

    This function returns ``None`` for the original process and for subinterpreters.
    After a fork or spawn, this returns the runtime ID of the immediate parent process.

    Note: Subinterpreters always return ``None`` as they have no parent/child relationship,
    even when one subinterpreter creates another.
    """
    global _PARENT_RUNTIME_ID

    # Lazily initialize from environment if not yet loaded
    if _PARENT_RUNTIME_ID is None:
        _initialize_from_env()

    return _PARENT_RUNTIME_ID
