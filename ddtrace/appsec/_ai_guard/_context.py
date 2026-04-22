"""Context variable for AI Guard collision avoidance.

When a framework integration (e.g. LangChain) already evaluates messages
through AI Guard, provider-level integrations (e.g. OpenAI) must skip
their own evaluation to avoid double-scanning.  The framework sets
``_ai_guard_active`` to ``True`` around its dispatch + LLM call block;
the provider listener checks it and returns early when active.

The variable is task-local (``contextvars``), so it works correctly
with both ``asyncio`` and threading.
"""

import contextlib
import contextvars


_ai_guard_active: contextvars.ContextVar[bool] = contextvars.ContextVar("_ai_guard_active", default=False)


def set_aiguard_context_active() -> contextvars.Token:
    """Mark the current task as already under AI Guard evaluation."""
    return _ai_guard_active.set(True)


def is_aiguard_context_active() -> bool:
    """Return ``True`` if a framework-level AI Guard evaluation is in progress."""
    return _ai_guard_active.get()


def reset_aiguard_context_active(token: contextvars.Token) -> None:
    """Restore the previous value after the framework evaluation is done.

    When the reset runs in a different ``Context`` than the one that created
    the token — e.g. LangChain streams drive iteration via ``context.run``,
    so the stream finalizer runs in a copied Context — ``ContextVar.reset``
    raises ``ValueError``. In that case, clear the flag in the current
    Context copy instead so provider integrations do not observe a stale
    "active" value for the remainder of this copy's lifetime.
    """
    try:
        _ai_guard_active.reset(token)
    except ValueError:
        _ai_guard_active.set(False)


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
    """One-shot AI Guard context guard for streaming flows.

    Stream finalization can fire from multiple terminal events — exhaustion,
    error, early break (generator ``finally`` on close), and weakref GC
    (never-consumed abandonment). ``reset()`` is idempotent: only the first
    call takes effect. Without this, the second reset either raises
    ``ValueError`` on the already-consumed token or degrades to ``set(False)``,
    which leaves an unreferenced token in the current Context.
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
