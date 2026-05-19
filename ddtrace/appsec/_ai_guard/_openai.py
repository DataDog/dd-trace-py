"""Shared AI Guard helpers for OpenAI SDK integrations.

The Chat Completions and Responses API integrations live in
``_openai_chat.py`` and ``_openai_responses.py`` respectively. This module
hosts state and helpers that both share — the lazy-built
``OpenAIAIGuardAbortError`` class (one cached instance so cross-surface
``isinstance`` checks succeed), the abort-error wrapper, and the dict-or-attr
accessor used by both converter families.
"""

from ddtrace.appsec._ai_guard._common import _get  # re-exported for _openai_chat / _openai_responses
from ddtrace.appsec._ai_guard._common import build_compound_abort_error_cls
from ddtrace.appsec._ai_guard._common import wrap_abort_error
from ddtrace.appsec.ai_guard._api_client import AIGuardAbortError
import ddtrace.internal.logger as ddlogger


logger = ddlogger.get_logger(__name__)

__all__ = ["_get", "_get_openai_abort_error_cls", "_wrap_abort_error"]


def _get_openai_abort_error_cls():
    """Return the OpenAI-compatible compound abort error class, or ``None`` if openai is not importable."""
    try:
        import openai
    except ImportError:
        return None
    return build_compound_abort_error_cls("OpenAI", openai.UnprocessableEntityError)


def _wrap_abort_error(cause: AIGuardAbortError) -> AIGuardAbortError:
    """Wrap an ``AIGuardAbortError`` into the OpenAI-compatible variant.

    Returns the original ``cause`` unchanged when the OpenAI SDK is not
    importable — the listener still surfaces a block, just without OpenAI
    exception-hierarchy compatibility (this matches the AI Guard contract:
    catch-by-``AIGuardAbortError`` always works, OpenAI-style ``except
    UnprocessableEntityError`` is a convenience for users who already speak
    the SDK's error vocabulary).
    """
    return wrap_abort_error(cause, _get_openai_abort_error_cls())
