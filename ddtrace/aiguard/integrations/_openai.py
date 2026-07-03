"""Shared AI Guard helpers for OpenAI SDK integrations.

The Chat Completions and Responses API integrations live in
``_openai_chat.py`` and ``_openai_responses.py`` respectively. This module
hosts the abort-error wrapper and the dict-or-attr accessor used by both
converter families. The OpenAI-compatible abort class lives in
``_openai_errors.py`` so importing this module does not eagerly import the
optional OpenAI SDK.
"""

from ddtrace.aiguard._api_client import AIGuardAbortError
from ddtrace.aiguard._common import wrap_provider_abort_error


__all__ = ["_wrap_abort_error"]


def _wrap_abort_error(cause: AIGuardAbortError) -> AIGuardAbortError:
    """Wrap *cause* so it satisfies both ``except AIGuardAbortError`` and
    ``except openai.UnprocessableEntityError``.

    Falls back to *cause* unchanged when the OpenAI SDK is not importable;
    catch-by-``AIGuardAbortError`` still works either way.
    """
    return wrap_provider_abort_error(
        cause, "ddtrace.aiguard.integrations._openai_errors", "OpenAIAIGuardAbortError", "OpenAI"
    )
