"""OpenAI-compatible AI Guard abort errors.

.. warning::

    **MANDATORY: import this module LAZILY (inside a function body), NEVER at
    module top level.** It imports the optional ``openai`` SDK eagerly at import
    time. A top-level import from any module that participates in OpenAI
    instrumentation setup (listeners, patch hooks, ``ddtrace.contrib`` glue)
    forces ``openai`` to load before our monkey-patches are in place and BREAKS
    OpenAI instrumentation -- spans go missing and AI Guard before/after hooks
    silently no-op.

    The only supported entry point is :func:`_get_openai_abort_error_cls` in
    ``_openai.py``, which performs the import inside a ``try`` block at call
    time. Do not bypass it.
"""

from typing import Any

import openai

from ddtrace.appsec.ai_guard._api_client import AIGuardAbortError


class OpenAIAIGuardAbortError(openai.UnprocessableEntityError, AIGuardAbortError):  # type: ignore[misc]
    """AI Guard block error that also satisfies the OpenAI SDK hierarchy.

    AIDEV-NOTE: catchability asymmetry vs plain ``AIGuardAbortError``.
    ``AIGuardAbortError`` derives from ``DDBlockException(BaseException)`` so a
    generic ``except Exception:`` does NOT catch it. This subclass also inherits
    from OpenAI's ``UnprocessableEntityError``, which is ``Exception``-derived,
    so it IS catchable by ``except Exception:``. That asymmetry is intentional:
    OpenAI users' existing ``except openai.APIError`` blocks should keep working.
    Code that wants uniform block detection should branch on
    ``isinstance(e, AIGuardAbortError)``.
    """

    def __init__(
        self,
        action: str,
        reason: str,
        tags: Any = None,
        sds: Any = None,
        tag_probs: Any = None,
    ) -> None:
        self.action = action
        self.reason = reason
        self.tags = tags
        self.sds = sds or []
        self.tag_probs = tag_probs

        message = f"AIGuardAbortError(action='{action}', reason='{reason}', tags='{tags}')"
        # AIDEV-NOTE: We can't call the SDK ``UnprocessableEntityError.__init__``
        # here. Its MRO super() chain ends up at ``AIGuardAbortError.__init__``
        # (which requires ``(action, reason)``), not ``Exception.__init__``, so
        # the SDK's ``APIError`` initializer raises ``TypeError: missing
        # 'reason'``. Initialize the SDK-side attributes directly instead. The
        # block originates from AI Guard, not an HTTP call, so ``response`` /
        # ``request`` / ``request_id`` are ``None``; only ``status_code`` and
        # ``body`` carry semantic meaning.
        self.message = message
        self.request = None
        self.body = {"action": action, "reason": reason, "source": "datadog_ai_guard"}
        self.code = "ai_guard_block"
        self.param = None
        self.type = "ai_guard_abort"
        self.response = None
        self.status_code = 422
        self.request_id = None
        Exception.__init__(self, message)


# Preserve historical span ``error.type`` values after moving the concrete class
# out of ``_openai.py``.
OpenAIAIGuardAbortError.__module__ = "ddtrace.appsec._ai_guard._openai"


__all__ = ["OpenAIAIGuardAbortError"]
