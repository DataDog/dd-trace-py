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

_DEPTHS = {
    Phase.REQUEST: contextvars.ContextVar("ai_guard_request_depth", default=0),
    Phase.RESPONSE: contextvars.ContextVar("ai_guard_response_depth", default=0),
}

# Opaque pairing handle returned by set / consumed by reset.
PhaseTokens = tuple[tuple[Phase, contextvars.Token[int]], ...]


def is_aiguard_context_active(phase: Optional[Phase] = None) -> bool:
    """Return whether a framework already covers phase.

    Omitting phase asks whether any phase is covered. Callers that guard a
    specific evaluation should always name their phase; the phase-less form
    exists for callers that only need to know an evaluation is in flight.
    """
    if phase is None:
        return any(var.get() > 0 for var in _DEPTHS.values())
    return _DEPTHS[phase].get() > 0


def set_aiguard_context_active(*phases: Phase) -> PhaseTokens:
    """Claim phases for the current execution context.

    No arguments claims every phase. Returns a handle to pair with
    reset_aiguard_context_active; nested claims increment the same counters, so
    reads stay true until every claim is released.
    """
    return tuple((phase, _DEPTHS[phase].set(_DEPTHS[phase].get() + 1)) for phase in (phases or ALL_PHASES))


def _decrement(phase: Phase) -> None:
    """Lower one phase's counter, never below zero."""
    var = _DEPTHS[phase]
    depth = var.get()
    if depth > 0:
        var.set(depth - 1)


def reset_aiguard_context_active(tokens: Optional[PhaseTokens]) -> None:
    """Release the claims recorded in tokens. A falsy handle is a no-op."""
    if not tokens:
        return
    for phase, token in reversed(tokens):
        try:
            _DEPTHS[phase].reset(token)
        except ValueError:
            # The token was created in a different Context -- a framework whose
            # before- and after-hooks landed in different asyncio tasks. Raising
            # here would escape into the framework's cleanup path, so fall back
            # to a plain decrement, which is correct whether this context
            # inherited the claim or never saw it (APPSEC-70282).
            _decrement(phase)


def reset_aiguard_context_active_current(*phases: Phase) -> None:
    """Tokenless release, for callers that cannot hold a token.

    A framework's after-event listener releasing what its before-event listener
    claimed has no way to thread the token through the dispatch. Safe to call
    when nothing is claimed: the counters never go below zero, so an after-event
    firing without a matching before-event cannot corrupt the state.
    """
    for phase in phases or ALL_PHASES:
        _decrement(phase)


@contextlib.contextmanager
def aiguard_context(*phases: Phase) -> Iterator[None]:
    """Claim phases for the duration of the block. No arguments claims every phase."""
    tokens = set_aiguard_context_active(*phases)
    try:
        yield
    finally:
        reset_aiguard_context_active(tokens)
