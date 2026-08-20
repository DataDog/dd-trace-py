import typing as t
import uuid
import weakref

from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env


log = get_logger(__name__)


__all__ = [
    "get_ancestor_runtime_id",
    "get_process_role",
    "get_runtime_id",
    "get_parent_runtime_id",
    "get_runtime_propagation_envs",
    "refresh_identity",
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
# Keep the finalizer objects alive for as long as their subscriber refs are registered.
# The finalizers prune dead refs even if the process never calls refresh_identity().
_ON_RUNTIME_ID_CHANGE_FINALIZERS: dict["weakref.ReferenceType[t.Callable[[str], None]]", t.Any] = {}
# A refresh is a single state transition: rotate the ID, then notify every live subscriber.
# Serialize the whole transition so two callers cannot leave subscribers rebuilt for an older ID.
_RUNTIME_ID_REFRESH_LOCK = forksafe.Lock()


def _discard_runtime_id_subscriber_ref(ref: "weakref.ReferenceType[t.Callable[[str], None]]") -> None:
    _ON_RUNTIME_ID_CHANGE.discard(ref)
    finalizer = _ON_RUNTIME_ID_CHANGE_FINALIZERS.pop(ref, None)
    if finalizer is not None:
        # Manual pruning owns cleanup from here; do not let the finalizer run later for the
        # same ref.
        finalizer.detach()


def _remove_runtime_id_subscriber(ref: "weakref.ReferenceType[t.Callable[[str], None]]") -> None:
    _discard_runtime_id_subscriber_ref(ref)


def _weakref_runtime_id_subscriber(
    cb: t.Callable[[str], None],
) -> t.Optional[tuple["weakref.ReferenceType[t.Callable[[str], None]]", t.Any]]:
    finalizer_target = cb
    if hasattr(cb, "__self__"):
        try:
            method_ref = t.cast("weakref.ReferenceType[t.Callable[[str], None]]", weakref.WeakMethod(cb))
            finalizer_target = getattr(cb, "__self__")
            return method_ref, weakref.finalize(finalizer_target, _remove_runtime_id_subscriber, method_ref)
        except TypeError:
            # Builtins and some C-extension callables expose __self__ but are not Python
            # bound methods. They may still support a plain weakref.
            pass

    try:
        plain_ref = weakref.ref(cb)
        return plain_ref, weakref.finalize(finalizer_target, _remove_runtime_id_subscriber, plain_ref)
    except TypeError:
        return None


def on_runtime_id_change(cb: t.Callable[[str], None]) -> None:
    """Register a callback to be called when refresh_identity() runs.

    refresh_identity() is the non-fork trigger for a new logical process
    instance. It is deliberately not called after a plain fork: forked children
    already get a fresh runtime ID silently (see _set_runtime_id()), and code
    that needs to react to a fork specifically should use forksafe.register().
    Only a weak reference to cb is kept, so the caller must keep it alive for
    it to keep firing.
    """
    global _ON_RUNTIME_ID_CHANGE
    subscriber = _weakref_runtime_id_subscriber(cb)
    if subscriber is None:
        # Some callables aren't weakrefable at all. Skip registration rather than crash
        # the caller's (often component-init) code path.
        log.debug("Could not weakly reference on_runtime_id_change() subscriber %r; skipping", cb)
        return
    ref, finalizer = subscriber
    _ON_RUNTIME_ID_CHANGE.add(ref)
    _ON_RUNTIME_ID_CHANGE_FINALIZERS[ref] = finalizer


def _regenerate_runtime_id() -> None:
    global _RUNTIME_ID
    _RUNTIME_ID = _generate_runtime_id()


def _notify_runtime_id_subscribers() -> None:
    global _ON_RUNTIME_ID_CHANGE
    dead = set()
    # Snapshot into a list before iterating: a concurrent on_runtime_id_change() (e.g. a
    # RemoteConfigClient/writer being constructed on another thread) mutating the live set
    # mid-iteration would otherwise raise "Set changed size during iteration".
    for ref in list(_ON_RUNTIME_ID_CHANGE):
        cb = ref()
        if cb is None:
            dead.add(ref)
            continue
        try:
            cb(_RUNTIME_ID)
        except Exception:
            # One broken subscriber must not prevent other subscribers from seeing the
            # refreshed runtime ID.
            log.debug("Error notifying on_runtime_id_change() subscriber", exc_info=True)
    for ref in dead:
        _discard_runtime_id_subscriber_ref(ref)


@forksafe.register
def _set_runtime_id() -> None:
    global _ANCESTOR_RUNTIME_ID, _PARENT_RUNTIME_ID

    # Save the runtime ID of the common ancestor of all processes.
    if _ANCESTOR_RUNTIME_ID is None:
        _ANCESTOR_RUNTIME_ID = _RUNTIME_ID

    _PARENT_RUNTIME_ID = _RUNTIME_ID
    # Does not notify on_runtime_id_change() subscribers: a fork has its own dedicated
    # forksafe hooks (per subscriber) for resetting fork-inherited state, which differs
    # from a plain rebuild-in-place (e.g. RemoteConfigClient's SHM-for-fork native client
    # must survive a fork untouched; see RemoteConfigPoller.reset_at_fork()).
    _regenerate_runtime_id()


def refresh_identity() -> None:
    """Regenerate the runtime ID without recording fork lineage.

    Unlike a fork, this does not update _PARENT_RUNTIME_ID / _ANCESTOR_RUNTIME_ID:
    the previous runtime ID was not a real parent process, so recording it there
    would make get_process_role() and friends misreport a fork lineage that never
    existed. Use this when a new logical process instance is created by a mechanism
    other than fork().
    """
    with _RUNTIME_ID_REFRESH_LOCK:
        _regenerate_runtime_id()
        _notify_runtime_id_subscribers()


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
