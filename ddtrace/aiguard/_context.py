"""Active-flag tracking for AI Guard collision avoidance.

When a framework integration (e.g. LangChain, Strands) is already evaluating
messages through AI Guard, provider-level integrations (e.g. OpenAI) must
skip their own evaluation to avoid double-scanning. The framework claims the
context around its model call and the provider listener calls
is_aiguard_context_active() to decide whether to short-circuit.

A claim is a shared object, so a release from another asyncio task (whose
Context is a copy) is seen by the task that claimed it.
"""

from collections.abc import Iterator
import contextlib
import contextvars
from typing import Optional


class _Claim:
    """One framework claim; every copy of the Context shares this object."""

    __slots__ = ("released",)

    def __init__(self) -> None:
        self.released = False


_CLAIMS: contextvars.ContextVar[tuple[_Claim, ...]] = contextvars.ContextVar("ai_guard_claims", default=())


def is_aiguard_context_active() -> bool:
    """Return True if a framework-level AI Guard evaluation is in progress."""
    for claim in _CLAIMS.get():
        if not claim.released:
            return True
    return False


def set_aiguard_context_active() -> _Claim:
    """Mark the current execution context as under AI Guard evaluation; return the handle that releases it."""
    claim = _Claim()
    # Drop claims released from another context: that release could not prune this context's copy.
    _CLAIMS.set(tuple(c for c in _CLAIMS.get() if not c.released) + (claim,))
    return claim


def reset_aiguard_context_active(claim: Optional[_Claim]) -> None:
    """Release claim, from any context. None is a no-op, and other claims stay held."""
    if claim is None:
        return
    claim.released = True
    claims = _CLAIMS.get()
    live = tuple(c for c in claims if not c.released)
    if len(live) != len(claims):
        _CLAIMS.set(live)


@contextlib.contextmanager
def aiguard_context() -> Iterator[None]:
    """Mark the current task as under AI Guard evaluation for the block's duration."""
    claim = set_aiguard_context_active()
    try:
        yield
    finally:
        reset_aiguard_context_active(claim)
