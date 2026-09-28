"""Phase-scoped collision avoidance for AI Guard.

A framework integration (LangChain, Strands) and a provider integration (OpenAI,
Anthropic) can both fire for the same call. The framework marks the phases it
evaluates itself, and a provider listener skips only the phase it is asked about
-- so a framework that covers the request but not the response leaves the
provider's response protection in place.

The phases are tracked independently because the split is what the coverage
depends on. LangChain streaming evaluates the request itself but has no
after-event to evaluate the response on, so it claims REQUEST only and the
provider's buffered stream still scans the response (APPSEC-70286). A single
all-or-nothing flag suppressed both and left streamed responses unevaluated.

Who claims what:

- LangChain generate / agenerate: REQUEST and RESPONSE (it evaluates both).
- LangChain streaming: REQUEST only.
- Strands: REQUEST and RESPONSE (before- and after-model-call hooks).

Claims are shared objects rather than per-context counters: an asyncio task
works on a copy of its parent's Context, so a counter lowered from another task
would leave the claiming task covered for good.
"""

from collections.abc import Iterator
import contextlib
import contextvars
from enum import Enum
from typing import Optional


class Phase(Enum):
    """Half of a model call a framework may take responsibility for."""

    REQUEST = "request"
    RESPONSE = "response"


ALL_PHASES: tuple[Phase, ...] = (Phase.REQUEST, Phase.RESPONSE)


class _Claim:
    """One phase held by one framework call.

    Every Context copied while the claim is held shares this object, so a
    release from any of them -- including a different asyncio task -- is seen by
    all, rather than only lowering the releasing task's own copy (APPSEC-70282).
    """

    __slots__ = ("phase", "released")

    def __init__(self, phase: Phase) -> None:
        self.phase = phase
        self.released = False


_CLAIMS: contextvars.ContextVar[tuple[_Claim, ...]] = contextvars.ContextVar("ai_guard_claims", default=())

# Opaque pairing handle returned by set / consumed by reset.
PhaseTokens = tuple[_Claim, ...]


def _prune() -> None:
    """Drop released claims from the current context's stack."""
    claims = _CLAIMS.get()
    live = tuple(claim for claim in claims if not claim.released)
    if len(live) != len(claims):
        _CLAIMS.set(live)


def is_aiguard_context_active(phase: Optional[Phase] = None) -> bool:
    """Return whether a framework already covers phase.

    Omitting phase asks whether any phase is covered. Callers that guard a
    specific evaluation should always name their phase; the phase-less form
    exists for callers that only need to know an evaluation is in flight.
    """
    return any(not claim.released and (phase is None or claim.phase is phase) for claim in _CLAIMS.get())


def set_aiguard_context_active(*phases: Phase) -> PhaseTokens:
    """Claim phases for the current execution context.

    No arguments claims every phase. Returns a handle to pair with
    reset_aiguard_context_active; nested claims stack, so reads stay true until
    every claim is released.
    """
    tokens = tuple(_Claim(phase) for phase in (phases or ALL_PHASES))
    _CLAIMS.set(tuple(claim for claim in _CLAIMS.get() if not claim.released) + tokens)
    return tokens


def reset_aiguard_context_active(tokens: Optional[PhaseTokens]) -> None:
    """Release the claims in tokens, from any context. A falsy handle is a no-op.

    Only these claims are released, so a release that lands in another task
    can neither leave the claiming task covered nor drop an unrelated claim.
    """
    if not tokens:
        return
    for claim in tokens:
        claim.released = True
    _prune()


def reset_aiguard_context_active_current(*phases: Phase) -> None:
    """Tokenless release of the most recent claim of each phase in this context.

    For a framework's after-event listener, which has no way to receive the
    token its before-event listener got back. A no-op when nothing is claimed,
    so an after-event firing without a matching before-event is harmless.
    """
    claims = _CLAIMS.get()
    for phase in phases or ALL_PHASES:
        for claim in reversed(claims):
            if not claim.released and claim.phase is phase:
                claim.released = True
                break
    _prune()


@contextlib.contextmanager
def aiguard_context(*phases: Phase) -> Iterator[None]:
    """Claim phases for the duration of the block. No arguments claims every phase."""
    tokens = set_aiguard_context_active(*phases)
    try:
        yield
    finally:
        reset_aiguard_context_active(tokens)
