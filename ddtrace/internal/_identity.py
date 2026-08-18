import threading
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
    """Register a callback to be called when ``refresh_identity()`` runs.

    ``refresh_identity()`` is the non-fork trigger (e.g. an AWS Lambda MicroVM
    ``/run`` hook) for a new logical process instance. It is deliberately not
    called after a plain fork: forked children already get a fresh runtime ID
    silently (see ``_set_runtime_id()``), and code that needs to react to a
    fork specifically should use ``forksafe.register()`` instead -- unlike a
    fork, no state (spans, SHM-backed native clients, ...) was inherited from
    a different process here, so subscribers generally only need to rebuild
    what bakes the runtime/client id in at construction, not reset buffers or
    handles the way a fork hook would. Only a weak reference to ``cb`` is
    kept, so the caller must keep it alive (e.g. by registering a bound
    method of a long-lived object) for it to keep firing.
    """
    global _ON_RUNTIME_ID_CHANGE
    try:
        ref = weakref.WeakMethod(cb) if hasattr(cb, "__self__") else weakref.ref(cb)
    except TypeError:
        # Some callables with a __self__ (e.g. certain C-implemented bound methods) aren't
        # compatible with WeakMethod, and some objects aren't weakrefable at all. Skip
        # registration rather than crash the caller's (often component-init) code path.
        log.debug("Could not weakly reference on_runtime_id_change() subscriber %r; skipping", cb)
        return
    _ON_RUNTIME_ID_CHANGE.add(ref)


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
            # This can run on a web framework's request-dispatch path (maybe_refresh_identity());
            # one broken subscriber must not take down the request or block the others.
            log.debug("Error notifying on_runtime_id_change() subscriber", exc_info=True)
    _ON_RUNTIME_ID_CHANGE -= dead


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

    Unlike a fork, this does not update ``_PARENT_RUNTIME_ID`` /
    ``_ANCESTOR_RUNTIME_ID``: the previous runtime ID was not a real parent
    process, so recording it there would make ``get_process_role()`` and
    friends misreport a fork lineage that never existed. Use this when a new
    logical process instance is created by a mechanism other than ``fork()``
    -- e.g. an AWS Lambda MicroVM instance launched from a shared image
    snapshot.
    """
    _regenerate_runtime_id()
    _notify_runtime_id_subscribers()


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
# Multiple instrumented request layers can observe the same MicroVM /run hook
# (for example, Werkzeug's http.server layer plus Flask). Refresh identity once
# per process so a single logical MicroVM instance gets one runtime-id rotation.
_IDENTITY_REFRESH_HOOK_REFRESHED = threading.Event()
_IDENTITY_REFRESH_HOOK_REFRESH_LOCK = threading.Lock()


def maybe_refresh_identity(method: t.Optional[str], path: t.Optional[str]) -> None:
    """Call refresh_identity() if this request is the AWS Lambda MicroVM "/run" hook.

    Called from every instrumented web framework's request-dispatch patch with that
    request's method and path, so the platform-defined hook path only needs to be
    matched in one place. A no-op outside a MicroVM (see _IS_AWS_LAMBDA_MICROVM).
    ``method``/``path`` may be ``None`` -- some callers read them straight off a raw
    request/environ mapping (e.g. ``environ.get("REQUEST_METHOD")``) that has no guarantee
    either key is present.
    """
    if not method or not path:
        return
    if not _IS_AWS_LAMBDA_MICROVM or method != MICROVM_RUN_HOOK_METHOD or path != MICROVM_RUN_HOOK_PATH:
        return

    with _IDENTITY_REFRESH_HOOK_REFRESH_LOCK:
        if _IDENTITY_REFRESH_HOOK_REFRESHED.is_set():
            return
        _IDENTITY_REFRESH_HOOK_REFRESHED.set()

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
