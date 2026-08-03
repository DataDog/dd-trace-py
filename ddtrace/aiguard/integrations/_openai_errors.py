"""OpenAI-compatible AI Guard abort errors.

.. warning::

    **MANDATORY: import this module LAZILY (inside a function body), NEVER at
    module top level.** It imports the optional ``openai`` SDK eagerly at import
    time. A top-level import from any module that participates in OpenAI
    instrumentation setup (listeners, patch hooks, ``ddtrace.contrib`` glue)
    forces ``openai`` to load before our monkey-patches are in place and BREAKS
    OpenAI instrumentation -- spans go missing and AI Guard before/after hooks
    silently no-op.

    The only supported entry point is :func:`_wrap_abort_error` in
    ``_openai.py``, which performs the import inside a ``try`` block at call
    time. Do not bypass it.
"""

import openai

from ddtrace.aiguard._common import make_provider_abort_error


# AIDEV-NOTE: catchability asymmetry vs plain ``AIGuardAbortError``.
# ``AIGuardAbortError`` derives from ``DDBlockException(BaseException)`` so a generic
# ``except Exception:`` does NOT catch it. This subclass also inherits from OpenAI's
# ``UnprocessableEntityError`` (``Exception``-derived), so it IS catchable by
# ``except Exception:``. That asymmetry is intentional: OpenAI users' existing
# ``except openai.APIError`` blocks should keep working. Code that wants uniform
# block detection should branch on ``isinstance(e, AIGuardAbortError)``.
#
# The ``__module__`` is stamped to preserve historical span ``error.type`` values.
OpenAIAIGuardAbortError = make_provider_abort_error(
    "OpenAIAIGuardAbortError",
    openai.UnprocessableEntityError,
    "ddtrace.aiguard.integrations.openai",
)


__all__ = ["OpenAIAIGuardAbortError"]
