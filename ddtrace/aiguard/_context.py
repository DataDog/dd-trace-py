"""Phase-scoped collision avoidance for AI Guard.

A framework integration (LangChain, Strands) claims the phases of a model call it evaluates itself, and a
provider integration (OpenAI, Anthropic) skips only the phase it checks. A claim is a shared object, so a
release from another asyncio task is seen by the task that claimed it.
"""

from collections.abc import Iterator
import contextlib
import contextvars
from enum import Enum
from typing import Optional


class Phase(Enum):
    """The half of a model call a framework can evaluate itself."""

    REQUEST = "request"
    RESPONSE = "response"


ALL_PHASES = frozenset(Phase)


class _Claim:
    """The phases one framework call holds; every copy of the Context shares this object."""

    __slots__ = ("phases", "released")

    def __init__(self, phases: frozenset[Phase]) -> None:
        self.phases = phases
        self.released = False


_CLAIMS: contextvars.ContextVar[tuple[_Claim, ...]] = contextvars.ContextVar("ai_guard_claims", default=())


def _release(claim: _Claim) -> None:
    claim.released = True
    claims = _CLAIMS.get()
    live = tuple(c for c in claims if not c.released)
    if len(live) != len(claims):
        _CLAIMS.set(live)


def is_aiguard_context_active(phase: Optional[Phase] = None) -> bool:
    """Return True if a framework holds phase (any phase when phase is None)."""
    claims = _CLAIMS.get()
    if not claims:
        return False
    for claim in claims:
        if not claim.released and (phase is None or phase in claim.phases):
            return True
    return False


def set_aiguard_context_active(*phases: Phase) -> _Claim:
    """Claim phases (all phases when none are given) and return the handle that releases them."""
    claim = _Claim(frozenset(phases) if phases else ALL_PHASES)
    # Drop claims released from another context: that release could not prune this context's copy.
    _CLAIMS.set(tuple(c for c in _CLAIMS.get() if not c.released) + (claim,))
    return claim


def reset_aiguard_context_active(claim: Optional[_Claim]) -> None:
    """Release claim from any context. None is a no-op."""
    if claim is not None:
        _release(claim)


def reset_aiguard_context_active_current(*phases: Phase) -> None:
    """Release the newest claim of exactly these phases in this context (all phases when none are given).

    For a framework whose claim and release run in the same task but in separate listeners, so no handle
    is passed between them. No-op when no such claim is held.
    """
    wanted = frozenset(phases) if phases else ALL_PHASES
    for claim in reversed(_CLAIMS.get()):
        if not claim.released and claim.phases == wanted:
            _release(claim)
            return


@contextlib.contextmanager
def aiguard_context(*phases: Phase) -> Iterator[None]:
    """Claim phases (all phases when none are given) for the duration of the block."""
    claim = set_aiguard_context_active(*phases)
    try:
        yield
    finally:
        reset_aiguard_context_active(claim)
