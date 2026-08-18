import typing as t
import uuid
import weakref

from ddtrace.internal import forksafe
from ddtrace.internal.settings import env


__all__ = [
    "get_ancestor_runtime_id",
    "get_process_role",
    "get_runtime_id",
    "get_parent_runtime_id",
    "get_runtime_propagation_envs",
    "refresh_identity",
    "maybe_refresh_identity",
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
# Held as weak references: subscribers are typically long-lived singletons or objects
# owned elsewhere (RemoteConfigClient, TelemetryWriter, trace writer instances), and tests
# construct many short-lived instances of some of these. A strong reference here would keep
# every instance ever constructed alive for the life of the process.
_ON_RUNTIME_ID_CHANGE: t.Set["weakref.ReferenceType[t.Callable[[str], None]]"] = set()  # noqa: UP006


def on_runtime_id_change(cb: t.Callable[[str], None]) -> None:
    """Register a callback to be called when the runtime ID changes.

    This can happen after a fork, or when ``refresh_identity()`` is called
    (e.g. from an AWS Lambda MicroVM ``/run`` hook). Only a weak reference to
    ``cb`` is kept, so the caller must keep it alive (e.g. by registering a
    bound method of a long-lived object) for it to keep firing.
    """
    global _ON_RUNTIME_ID_CHANGE
    ref = weakref.WeakMethod(cb) if hasattr(cb, "__self__") else weakref.ref(cb)
    _ON_RUNTIME_ID_CHANGE.add(ref)


def _regenerate_runtime_id() -> None:
    global _RUNTIME_ID, _ON_RUNTIME_ID_CHANGE
    _RUNTIME_ID = _generate_runtime_id()
    dead = set()
    # Snapshot into a list before iterating: a concurrent on_runtime_id_change() (e.g. a
    # RemoteConfigClient/writer being constructed on another thread) mutating the live set
    # mid-iteration would otherwise raise "Set changed size during iteration".
    for ref in list(_ON_RUNTIME_ID_CHANGE):
        cb = ref()
        if cb is None:
            dead.add(ref)
            continue
        cb(_RUNTIME_ID)
    _ON_RUNTIME_ID_CHANGE -= dead


@forksafe.register
def _set_runtime_id() -> None:
    global _ANCESTOR_RUNTIME_ID, _PARENT_RUNTIME_ID

    # Save the runtime ID of the common ancestor of all processes.
    if _ANCESTOR_RUNTIME_ID is None:
        _ANCESTOR_RUNTIME_ID = _RUNTIME_ID

    _PARENT_RUNTIME_ID = _RUNTIME_ID
    _regenerate_runtime_id()


def refresh_identity() -> None:
    """Regenerate the runtime ID without recording fork lineage.

    Unlike a fork, this does not update ``_PARENT_RUNTIME_ID`` /
    ``_ANCESTOR_RUNTIME_ID``: the previous runtime ID was not a real parent
    process, so recording it there would make ``get_process_role()`` and
    friends misreport a fork lineage that never existed. Use this when a new
    logical process instance is created by a mechanism other than ``fork()``
    -- e.g. an AWS Lambda MicroVM instance launched from a shared image
    snapshot.
    """
    _regenerate_runtime_id()


# Fixed platform path for the AWS Lambda MicroVM "/run" lifecycle hook. Never the "/resume"
# hook path -- see refresh_identity()'s docstring for why. Exported (no leading underscore)
# so tests can reference the same constant instead of duplicating the literal.
MICROVM_RUN_HOOK_METHOD = "POST"
MICROVM_RUN_HOOK_PATH = "/aws/lambda-microvms/runtime/v1/run"

# Same env var #18017 uses to detect a MicroVM at runtime (rand64bits() OS-entropy fallback).
# Read once at import, like that fix does, so maybe_refresh_identity() is a single cached
# comparison off the hot request path everywhere else, and a true no-op outside a MicroVM --
# this exact method+path is otherwise just an unauthenticated trigger on every ddtrace user's
# request-dispatch path, MicroVM or not.
_IS_AWS_LAMBDA_MICROVM = env.get("AWS_LAMBDA_MICROVM_IMAGE_ARN") is not None


def maybe_refresh_identity(method: str, path: str) -> None:
    """Call refresh_identity() if this request is the AWS Lambda MicroVM "/run" hook.

    Called from every instrumented web framework's request-dispatch patch with that
    request's method and path, so the platform-defined hook path only needs to be
    matched in one place. A no-op outside a MicroVM (see _IS_AWS_LAMBDA_MICROVM).
    """
    if _IS_AWS_LAMBDA_MICROVM and method == MICROVM_RUN_HOOK_METHOD and path == MICROVM_RUN_HOOK_PATH:
        refresh_identity()


def get_runtime_id() -> str:
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


def get_process_role() -> t.Optional[str]:
    """Return the role of this process in a forking framework.

    Returns ``'worker'`` if this process was forked from a parent (or spawned
    as a child via multiprocessing), ``'main'`` if this process has forked
    worker children, or ``None`` for a single-process application.
    """
    if _PARENT_RUNTIME_ID is not None:
        return "worker"
    if forksafe.has_forked():
        return "main"
    return None


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
