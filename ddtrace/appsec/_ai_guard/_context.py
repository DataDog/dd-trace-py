"""Active-flag tracking for AI Guard collision avoidance.

When a framework integration (e.g. LangChain) already evaluates messages
through AI Guard, provider-level integrations (e.g. OpenAI) must skip
their own evaluation to avoid double-scanning. The framework calls
``set_aiguard_context_active()`` around its dispatch + LLM call block;
the provider listener calls ``is_aiguard_context_active()`` and returns
early when active.

Implementation note — why not ``contextvars.ContextVar``:
    A ContextVar with ``set()`` / ``reset(token)`` semantics looks like
    the natural fit, and it auto-inherits into asyncio tasks. The
    problem is the *reset* side: ``ContextVar.reset(token)`` is bound to
    the originating Context, so when a stream finalizer fires inside a
    copied Context (LangChain iterates streams via
    ``copy_context().run(...)``), the reset cannot reach the parent
    Context. The parent observes ``active=True`` for the rest of its
    lifetime, silently disabling provider-level evaluation.

    We track active state with a per-owner counter instead, where the
    "owner" is the current asyncio task (if any) or thread. The token
    returned from ``set`` carries that owner, so a ``reset`` running in
    any Context still decrements the right counter.

Trade-off: spawning ``asyncio.create_task`` from inside an active
region does NOT auto-inherit ``active=True`` into the child task — the
child has its own owner key. Existing framework integrations don't
spawn child tasks during their evaluated lifecycle, so this is
acceptable.
"""

import asyncio
import contextlib
import threading


# Owner key. Asyncio tasks and threads use distinct prefixes so a thread id
# can never collide with a task id (`id(task)` and `threading.get_ident()`
# share a numeric range).
_OwnerKey = tuple[str, int]


_active_counts: dict[_OwnerKey, int] = {}
_active_lock = threading.Lock()


def _get_owner() -> _OwnerKey:
    """Identify the current asyncio task, falling back to the thread id."""
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    if task is not None:
        return ("task", id(task))
    return ("thread", threading.get_ident())


def set_aiguard_context_active() -> _OwnerKey:
    """Mark the current task / thread as already under AI Guard evaluation.

    Returns an opaque token (the owner key) to pair with
    ``reset_aiguard_context_active``. Nested set/reset pairs increment and
    decrement the same counter, so reads return ``True`` until every set is
    matched by a reset.
    """
    owner = _get_owner()
    with _active_lock:
        _active_counts[owner] = _active_counts.get(owner, 0) + 1
    return owner


def is_aiguard_context_active() -> bool:
    """Return ``True`` if a framework-level AI Guard evaluation is in progress."""
    return _active_counts.get(_get_owner(), 0) > 0


def reset_aiguard_context_active(token: _OwnerKey) -> None:
    """Decrement the active counter for the owner the matching ``set`` recorded.

    The token *is* the original owner key, so a reset running in a copied
    Context (LangChain stream finalizer, ``ctx.run(...)`` wrappers) still
    decrements the parent owner's counter — this is what avoids the
    cross-context-reset leak that ``ContextVar.reset`` is subject to.
    """
    if token is None:
        return
    with _active_lock:
        n = _active_counts.get(token, 0)
        if n > 1:
            _active_counts[token] = n - 1
        else:
            _active_counts.pop(token, None)


@contextlib.contextmanager
def aiguard_context():
    """Mark the current task as under AI Guard evaluation for the block's duration.

    Framework integrations (LangChain, Strands) wrap their dispatch + LLM
    call block with this so nested provider-level integrations (e.g. OpenAI)
    skip their own evaluation.
    """
    token = set_aiguard_context_active()
    try:
        yield
    finally:
        reset_aiguard_context_active(token)


class AIGuardStreamGuard:
    """One-shot AI Guard active guard for streaming flows.

    Stream finalization can fire from multiple terminal events — exhaustion,
    error, early break (generator ``finally`` on close), and weakref GC
    (never-consumed abandonment). ``reset()`` is idempotent: only the first
    call takes effect.
    """

    __slots__ = ("_token", "_done")

    def __init__(self) -> None:
        self._token = set_aiguard_context_active()
        self._done = False

    def reset(self) -> None:
        if self._done:
            return
        self._done = True
        reset_aiguard_context_active(self._token)
