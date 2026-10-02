"""Phase-scoped collision avoidance for AI Guard.

A framework integration (LangChain, Strands) and a provider integration (OpenAI,
Anthropic) can both fire for the same call. The framework marks the phases it
evaluates itself, and a provider listener skips only the phase it is asked about
-- so a framework that covers the request but not the response leaves the
provider's response protection in place.

The phases are tracked independently so a framework never suppresses a check it
does not perform itself. A single all-or-nothing flag once let LangChain
streaming switch off the provider's buffered-stream evaluation without doing its
own, and left streamed responses unevaluated (APPSEC-70286).

Who claims what:

- LangChain generate / agenerate: REQUEST and RESPONSE for the whole call (it
  evaluates both).
- LangChain model streams: REQUEST and RESPONSE while the model's own _stream /
  _astream is read. The response is buffered and evaluated there, below
  LangChain's callbacks and above the provider, so the provider's buffered
  stream stays passthrough and cannot trip LangChain's per-chunk timeout.
- Strands: REQUEST and RESPONSE (before- and after-model-call hooks).

Claims are shared objects rather than per-context counters: an asyncio task
works on a copy of its parent's Context, so a counter lowered from another task
would leave the claiming task covered for good. Every claim is released by the
handle set_aiguard_context_active returned, never by searching the current
context, so a release can neither miss its claim nor take someone else's. Prefer
aiguard_context, which claims and releases in one frame; carry the handle only
when a framework's hooks split claim and release (Strands).
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
    claims = _CLAIMS.get()
    if not claims:
        return False
    # Runs on every provider call: a plain loop is several times faster than any() over a generator.
    for claim in claims:
        if not claim.released and (phase is None or claim.phase is phase):
            return True
    return False


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


@contextlib.contextmanager
def aiguard_context(*phases: Phase) -> Iterator[None]:
    """Claim phases for the duration of the block. No arguments claims every phase."""
    tokens = set_aiguard_context_active(*phases)
    try:
        yield
    finally:
        reset_aiguard_context_active(tokens)
