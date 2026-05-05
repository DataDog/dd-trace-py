"""Active-flag tracking for AI Guard collision avoidance.

When a framework integration (e.g. LangChain, Strands) is already evaluating
messages through AI Guard, provider-level integrations (e.g. OpenAI) must
skip their own evaluation to avoid double-scanning. The framework calls
``set_aiguard_context_active()`` around its dispatch + LLM call block and
the provider listener calls ``is_aiguard_context_active()`` to decide
whether to short-circuit
"""

import contextlib
import contextvars
from typing import Optional


_AI_GUARD_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar("ai_guard_active_depth", default=0)


def is_aiguard_context_active() -> bool:
    """Return ``True`` if a framework-level AI Guard evaluation is in progress."""
    return _AI_GUARD_DEPTH.get() > 0


def set_aiguard_context_active() -> contextvars.Token:
    """Mark the current execution context as already under AI Guard evaluation.

    Returns an opaque :class:`contextvars.Token` to pair with
    :func:`reset_aiguard_context_active`. Nested set / reset pairs increment
    and decrement the same depth counter, so reads return ``True`` until every
    set is matched by a reset.
    """
    return _AI_GUARD_DEPTH.set(_AI_GUARD_DEPTH.get() + 1)


def reset_aiguard_context_active(token: Optional[contextvars.Token]) -> None:
    """Restore the depth counter to its value before the matching ``set``.

    A ``None`` token is a defensive no-op (e.g. cleanup paths that may run
    without a prior ``set``).
    """
    if token is None:
        return
    _AI_GUARD_DEPTH.reset(token)


def reset_aiguard_context_active_current() -> None:
    """Tokenless companion to :func:`reset_aiguard_context_active`.

    Decrements the depth counter for the current context. Used when the
    original token is not accessible — e.g. a framework's ``.after``
    listener releasing the counter that the matching ``.before`` listener
    bumped, since the dispatch infrastructure does not thread the token
    through to the after-event.

    Safe to call when the counter is already zero (no-op): the ``.after``
    event may fire without a matching ``.before`` if dispatch is
    reconfigured at runtime.
    """
    depth = _AI_GUARD_DEPTH.get()
    if depth > 0:
        _AI_GUARD_DEPTH.set(depth - 1)


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
