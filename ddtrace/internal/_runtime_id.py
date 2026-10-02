import logging
import typing as t
import uuid

from ddtrace.internal.constants import WEB_REQUEST_STARTING_EVENT
from ddtrace.internal.serverless import MICROVM_RUN_HOOK_METHOD
from ddtrace.internal.serverless import MICROVM_RUN_HOOK_PATH
from ddtrace.internal.serverless import in_aws_lambda_microvm
from ddtrace.internal.settings import env

from . import forksafe


log = logging.getLogger(__name__)


__all__ = [
    "get_ancestor_runtime_id",
    "get_process_role",
    "get_runtime_id",
    "get_parent_runtime_id",
    "get_runtime_propagation_envs",
    "listen_for_identity_refresh_hooks",
    "maybe_refresh_identity",
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
_ON_RUNTIME_ID_CHANGE: t.Set[t.Callable[[str], None]] = set()  # noqa: UP006
_ON_RUNTIME_IDENTITY_REFRESH: t.Set[t.Callable[[str], None]] = set()  # noqa: UP006
# MicroVM refreshes share this lock with consumers that must not observe a partially refreshed
# identity. Non-MicroVM callers do not acquire it.
_RUNTIME_IDENTITY_REFRESH_LOCK = forksafe.RLock()


def on_runtime_id_change(cb: t.Callable[[str], None]) -> None:
    """Register a callback to be called when the runtime ID changes.

    This can happen after a fork.
    """
    global _ON_RUNTIME_ID_CHANGE
    _ON_RUNTIME_ID_CHANGE.add(cb)


def on_runtime_identity_refresh(cb: t.Callable[[str], None]) -> None:
    """Register a callback to be called after an explicit runtime identity refresh.

    This is separate from the fork callback because a logical runtime replacement
    must not be treated as a child process or update the fork lineage.
    """
    global _ON_RUNTIME_IDENTITY_REFRESH
    _ON_RUNTIME_IDENTITY_REFRESH.add(cb)


def get_runtime_identity_refresh_lock() -> t.ContextManager[None]:
    """Return the lock that serializes a MicroVM identity refresh with its consumers."""
    return t.cast(t.ContextManager[None], _RUNTIME_IDENTITY_REFRESH_LOCK)


def _notify_runtime_id_callbacks(callbacks: t.Set[t.Callable[[str], None]]) -> None:  # noqa: UP006
    for cb in list(callbacks):
        try:
            cb(_RUNTIME_ID)
        except Exception:
            log.exception("Exception ignored in runtime ID callback %r", cb)


def _notify_runtime_identity_refresh_callbacks(
    *,
    callbacks: t.Optional[t.List[t.Callable[[str], None]]] = None,  # noqa: UP006
    raise_on_error: bool = False,
) -> None:  # noqa: UP006
    # Direct refresh callers keep subscriber failures isolated so every component gets
    # a chance to rebuild. The MicroVM coordinator opts into propagation so a failed
    # rebuild leaves its completion guard unset and the same identity can be retried.
    # A caller-supplied list is the MicroVM transition's pending work queue. The
    # permanent registry must remain unchanged so later refreshes still notify all
    # registered callbacks.
    # Only the MicroVM coordinator supplies a pending queue. It runs every pending callback
    # before propagating, so a callback that keeps failing cannot starve the ones behind it
    # on each retry.
    defer_errors = raise_on_error and callbacks is not None
    if callbacks is None:
        callbacks = list(_ON_RUNTIME_IDENTITY_REFRESH)

    first_error: t.Optional[Exception] = None
    for cb in list(callbacks):
        try:
            cb(_RUNTIME_ID)
        except Exception as e:
            if not raise_on_error:
                log.exception("Exception ignored in runtime ID callback %r", cb)
                continue
            if not defer_errors:
                raise
            if first_error is None:
                first_error = e
            log.exception("Runtime ID callback %r failed and stays pending", cb)
            continue

        # Removing only after return records completion; a raised callback stays pending.
        callbacks.remove(cb)

    if first_error is not None:
        raise first_error


def _refresh_runtime_id() -> None:
    global _RUNTIME_ID

    _RUNTIME_ID = _generate_runtime_id()
    _notify_runtime_id_callbacks(_ON_RUNTIME_ID_CHANGE)


@forksafe.register
def _set_runtime_id() -> None:
    global _RUNTIME_ID, _ANCESTOR_RUNTIME_ID, _PARENT_RUNTIME_ID

    # Save the runtime ID of the common ancestor of all processes.
    if _ANCESTOR_RUNTIME_ID is None:
        _ANCESTOR_RUNTIME_ID = _RUNTIME_ID

    _PARENT_RUNTIME_ID = _RUNTIME_ID
    _refresh_runtime_id()


def refresh_identity(
    raise_on_error: bool = False,
    *,
    _callback_snapshot: t.Optional[t.List[t.Callable[[str], None]]] = None,  # noqa: UP006
) -> None:  # noqa: UP006
    """Regenerate the runtime ID without recording fork lineage.

    Unlike a fork, this does not update _PARENT_RUNTIME_ID / _ANCESTOR_RUNTIME_ID:
    the previous runtime ID was not a real parent process, so recording it there
    would make get_process_role() and friends misreport a fork lineage that never
    existed. Use this when a new logical process instance is created by a mechanism
    other than fork().
    """
    # This is an opt-in lifecycle path: non-MicroVM processes do not call it from
    # ordinary request handling, so their runtime-ID and fork behavior is unchanged.
    # Notify consumers that only need the new ID first. The explicit refresh
    # callbacks below are for components that must rebuild restore-sensitive
    # state, which is different from the fork handling in _set_runtime_id().
    if in_aws_lambda_microvm():
        with _RUNTIME_IDENTITY_REFRESH_LOCK:
            _refresh_identity(raise_on_error, _callback_snapshot)
    else:
        _refresh_identity(raise_on_error, _callback_snapshot)


def _refresh_identity(
    raise_on_error: bool,
    callback_snapshot: t.Optional[t.List[t.Callable[[str], None]]],  # noqa: UP006
) -> None:  # noqa: UP006
    _refresh_runtime_id()
    if callback_snapshot is not None:
        # Replace the caller-owned queue after rotation. This preserves the normal
        # refresh ordering without duplicating callbacks if the list was reused.
        callback_snapshot[:] = _ON_RUNTIME_IDENTITY_REFRESH

    _notify_runtime_identity_refresh_callbacks(callbacks=callback_snapshot, raise_on_error=raise_on_error)


# Multiple request layers can observe the same /run hook. Refresh identity once per
# process so a single logical MicroVM instance gets one runtime-id rotation.
_IDENTITY_REFRESH_HOOK_REFRESHED = forksafe.Event()
_IDENTITY_REFRESH_HOOK_REFRESH_LOCK = forksafe.Lock()
# Keep a failed transition retryable without rotating the identity again.
_IDENTITY_REFRESH_HOOK_RUNTIME_ID: t.Optional[str] = None
# This is per-transition state, unlike _ON_RUNTIME_IDENTITY_REFRESH. Callbacks are
# removed here only after succeeding for _IDENTITY_REFRESH_HOOK_RUNTIME_ID.
_IDENTITY_REFRESH_HOOK_PENDING_CALLBACKS: t.Optional[t.List[t.Callable[[str], None]]] = None  # noqa: UP006


def listen_for_identity_refresh_hooks(
    on_event: t.Callable[[str, t.Callable[[t.Optional[str], t.Optional[str]], None]], None],
) -> None:
    """Refresh MicroVM identity from request events emitted before root span creation."""
    if not in_aws_lambda_microvm():
        return

    on_event(WEB_REQUEST_STARTING_EVENT, maybe_refresh_identity)


def maybe_refresh_identity(method: t.Optional[str], path: t.Optional[str]) -> None:
    """Call refresh_identity() if this request is the AWS Lambda MicroVM /run hook."""
    if not in_aws_lambda_microvm():
        return
    if not method or not path:
        return
    if method != MICROVM_RUN_HOOK_METHOD or path != MICROVM_RUN_HOOK_PATH:
        return

    global _IDENTITY_REFRESH_HOOK_PENDING_CALLBACKS, _IDENTITY_REFRESH_HOOK_RUNTIME_ID
    with _IDENTITY_REFRESH_HOOK_REFRESH_LOCK:
        if _IDENTITY_REFRESH_HOOK_REFRESHED.is_set():
            return

        # Rotate once per transition; after a callback failure, retry only callbacks that
        # did not complete against the current ID. The completion guard is set only after
        # every callback succeeds.
        if _IDENTITY_REFRESH_HOOK_RUNTIME_ID != _RUNTIME_ID:
            _IDENTITY_REFRESH_HOOK_PENDING_CALLBACKS = []
            # Keep refresh_identity() as the single refresh entry point. Its private
            # snapshot sink lets this hook retain progress when a subscriber fails.
            try:
                refresh_identity(raise_on_error=True, _callback_snapshot=_IDENTITY_REFRESH_HOOK_PENDING_CALLBACKS)
            except Exception:
                # The ID has already rotated before invoking explicit subscribers. Retry those
                # subscribers against that ID.
                _IDENTITY_REFRESH_HOOK_RUNTIME_ID = _RUNTIME_ID
                raise
            _IDENTITY_REFRESH_HOOK_RUNTIME_ID = _RUNTIME_ID
        else:
            # refresh_identity() releases this lock before we retry only the
            # callbacks that previously failed. Keep the retry serialized with
            # consumers of the refreshed identity as well.
            with _RUNTIME_IDENTITY_REFRESH_LOCK:
                _notify_runtime_identity_refresh_callbacks(
                    callbacks=_IDENTITY_REFRESH_HOOK_PENDING_CALLBACKS,
                    raise_on_error=True,
                )

        _IDENTITY_REFRESH_HOOK_REFRESHED.set()
        _IDENTITY_REFRESH_HOOK_PENDING_CALLBACKS = None


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
