import typing as t
import uuid

from ddtrace.internal.settings import env

from .. import forksafe


__all__ = [
    "get_ancestor_runtime_id",
    "get_runtime_id",
    "get_parent_runtime_id",
    "get_runtime_propagation_envs",
]


_ENV_ROOT_SESSION_ID = "_DD_ROOT_PY_SESSION_ID"
_ENV_PARENT_SESSION_ID = "_DD_PARENT_PY_SESSION_ID"


def _generate_runtime_id() -> str:
    return uuid.uuid4().hex


_RUNTIME_ID: str = _generate_runtime_id()
# Seeded from env vars when this process was spawned (multiprocessing spawn/forkserver).
# For fork-based processes these are set by _set_runtime_id() via the forksafe hook.
_ANCESTOR_RUNTIME_ID: t.Optional[str] = env.get(_ENV_ROOT_SESSION_ID)
_PARENT_RUNTIME_ID: t.Optional[str] = env.get(_ENV_PARENT_SESSION_ID)
# IMPORTANT: Do not change t.Set to set until minimum Python version is 3.11+
# Module-level set[...] in Python 3.10 affects import timing. See packages.py for details.
_ON_RUNTIME_ID_CHANGE: t.Set[t.Callable[[str], None]] = set()  # noqa: UP006


def on_runtime_id_change(cb: t.Callable[[str], None]) -> None:
    """Register a callback to be called when the runtime ID changes.

    This can happen after a fork.
    """
    global _ON_RUNTIME_ID_CHANGE
    _ON_RUNTIME_ID_CHANGE.add(cb)


@forksafe.register
def _set_runtime_id():
    global _RUNTIME_ID, _ANCESTOR_RUNTIME_ID, _PARENT_RUNTIME_ID

    # Save the runtime ID of the common ancestor of all processes.
    if _ANCESTOR_RUNTIME_ID is None:
        _ANCESTOR_RUNTIME_ID = _RUNTIME_ID

    _PARENT_RUNTIME_ID = _RUNTIME_ID
    _RUNTIME_ID = _generate_runtime_id()
    for cb in _ON_RUNTIME_ID_CHANGE:
        cb(_RUNTIME_ID)


def get_runtime_id():
    """Return a unique string identifier for this runtime.

    Do not store this identifier as it can change when, e.g., the process forks.
    """
    return _RUNTIME_ID


def get_ancestor_runtime_id() -> t.Optional[str]:
    """Return the runtime ID of the common ancestor of this process.

    Once this value is set (this will happen after a fork) it will not change
    for the lifetime of the process. This function returns ``None`` for the
    ancestor process.
    """
    return _ANCESTOR_RUNTIME_ID


def get_parent_runtime_id() -> t.Optional[str]:
    """Return the runtime ID of the parent process.

    Set after a fork or when seeded from the ``_DD_PARENT_PY_SESSION_ID`` environment
    variable (multiprocessing spawn/forkserver). Returns ``None`` in the root process.
    """
    return _PARENT_RUNTIME_ID


def get_runtime_propagation_envs() -> dict[str, str]:
    """Return session lineage env vars to inject into child process environments.

    These vars allow exec-based child processes (subprocess, multiprocessing spawn)
    to reconstruct the process lineage without relying on fork inheritance.
    """
    ancestor = get_ancestor_runtime_id()
    current = get_runtime_id()
    session_vars: dict[str, str] = {_ENV_ROOT_SESSION_ID: ancestor if ancestor is not None else current}
    if current is not None:
        session_vars[_ENV_PARENT_SESSION_ID] = current
    return session_vars
