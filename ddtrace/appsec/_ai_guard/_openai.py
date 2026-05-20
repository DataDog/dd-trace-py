"""Shared AI Guard helpers for OpenAI SDK integrations.

The Chat Completions and Responses API integrations live in
``_openai_chat.py`` and ``_openai_responses.py`` respectively. This module
hosts the abort-error wrapper and the dict-or-attr accessor used by both
converter families. The OpenAI-compatible abort class lives in
``_openai_errors.py`` so importing this module does not eagerly import the
optional OpenAI SDK.
"""

from ddtrace.appsec._ai_guard._common import wrap_abort_error
from ddtrace.appsec.ai_guard._api_client import AIGuardAbortError
import ddtrace.internal.logger as ddlogger


logger = ddlogger.get_logger(__name__)


__all__ = ["_wrap_abort_error"]


def _wrap_abort_error(cause: AIGuardAbortError) -> AIGuardAbortError:
    """Wrap *cause* so it satisfies both ``except AIGuardAbortError`` and
    ``except openai.UnprocessableEntityError``.

    Falls back to *cause* unchanged when the OpenAI SDK is not importable;
    catch-by-``AIGuardAbortError`` still works either way.
    """
    exception_class: type[AIGuardAbortError] = AIGuardAbortError
    try:
        # AIDEV-NOTE: import lazily -- ``_openai_errors`` pulls in the optional
        # OpenAI SDK at import time. Python's import lock guarantees all
        # concurrent cold imports observe the same class object.
        from ddtrace.appsec._ai_guard._openai_errors import OpenAIAIGuardAbortError

        exception_class = OpenAIAIGuardAbortError
    except ImportError:
        logger.warning(
            "AI Guard: failed to import the OpenAI SDK; falling back to bare "
            "AIGuardAbortError. Install ``openai`` to get SDK-hierarchy "
            "compatibility (``except openai.UnprocessableEntityError``)."
        )

    return wrap_abort_error(cause, exception_class)
