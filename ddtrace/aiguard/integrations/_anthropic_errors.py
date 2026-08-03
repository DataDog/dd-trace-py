"""Anthropic-compatible AI Guard abort errors.

.. warning::

    **MANDATORY: import this module LAZILY (inside a function body), NEVER at
    module top level.** It imports the optional ``anthropic`` SDK eagerly at
    import time. A top-level import from any module that participates in
    Anthropic instrumentation setup (listeners, patch hooks,
    ``ddtrace.contrib`` glue) forces ``anthropic`` to load before our
    monkey-patches are in place and BREAKS Anthropic instrumentation -- spans
    go missing and AI Guard before/after hooks silently no-op.

    The only supported entry point is :func:`_wrap_abort_error` in
    ``_anthropic.py``, which performs the import inside a ``try`` block at call
    time. Do not bypass it.
"""

import anthropic

from ddtrace.aiguard._common import make_provider_abort_error


# AIDEV-NOTE: catchability asymmetry vs plain ``AIGuardAbortError``.
# ``AIGuardAbortError`` derives from ``DDBlockException(BaseException)`` so a generic
# ``except Exception:`` does NOT catch it. This subclass also inherits from Anthropic's
# ``UnprocessableEntityError`` (``Exception``-derived), so it IS catchable by
# ``except Exception:``. That asymmetry is intentional: Anthropic users' existing
# ``except anthropic.APIError`` blocks should keep working. Code that wants uniform
# block detection should branch on ``isinstance(e, AIGuardAbortError)``.
#
# The ``__module__`` is stamped to preserve historical span ``error.type`` values.
AnthropicAIGuardAbortError = make_provider_abort_error(
    "AnthropicAIGuardAbortError",
    anthropic.UnprocessableEntityError,
    "ddtrace.aiguard.integrations.anthropic",
)


__all__ = ["AnthropicAIGuardAbortError"]
