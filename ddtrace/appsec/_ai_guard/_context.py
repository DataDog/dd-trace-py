"""Context variable for AI Guard collision avoidance.

When a framework integration (e.g. LangChain) already evaluates messages
through AI Guard, provider-level integrations (e.g. OpenAI) must skip
their own evaluation to avoid double-scanning.  The framework sets
``_ai_guard_active`` to ``True`` around its dispatch + LLM call block;
the provider listener checks it and returns early when active.

The variable is task-local (``contextvars``), so it works correctly
with both ``asyncio`` and threading.
"""

import contextvars


_ai_guard_active: contextvars.ContextVar[bool] = contextvars.ContextVar("_ai_guard_active", default=False)


def set_aiguard_context_active() -> contextvars.Token:
    """Mark the current task as already under AI Guard evaluation."""
    return _ai_guard_active.set(True)


def is_aiguard_context_active() -> bool:
    """Return ``True`` if a framework-level AI Guard evaluation is in progress."""
    return _ai_guard_active.get()


def reset_aiguard_context_active(token: contextvars.Token) -> None:
    """Restore the previous value after the framework evaluation is done."""
    _ai_guard_active.reset(token)
