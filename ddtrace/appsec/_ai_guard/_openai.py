"""Shared AI Guard helpers for OpenAI SDK integrations.

The Chat Completions and Responses API integrations live in
``_openai_chat.py`` and ``_openai_responses.py`` respectively. This module
hosts state and helpers that both share — the lazy-built
``OpenAIAIGuardAbortError`` class (one cached instance so cross-surface
``isinstance`` checks succeed), the abort-error wrapper, and the dict-or-attr
accessor used by both converter families.
"""

import threading

from ddtrace.appsec.ai_guard._api_client import AIGuardAbortError
import ddtrace.internal.logger as ddlogger


logger = ddlogger.get_logger(__name__)


# AIDEV-NOTE: The compound ``OpenAIAIGuardAbortError`` class is built lazily on
# first block. ``_openai.py`` is imported unconditionally by the AI Guard
# listener (see ``_listener.py``), but the OpenAI SDK is an optional runtime
# dependency — eagerly importing ``openai`` here would break AI Guard
# initialization in environments that only use a different provider.
#
_openai_abort_error_cls = None

# The lazy build uses double-checked locking: the unsynchronised check-then-act
# pattern would let two threads simultaneously enter the build branch and each
# create their own class, so callers that captured ``type(err)`` from one block
# would silently stop matching errors from a concurrent block.
_openai_abort_error_cls_lock = threading.Lock()


def _get_openai_abort_error_cls():
    """Return ``OpenAIAIGuardAbortError`` (cached), or ``None`` if openai is not importable.

    The class inherits from ``openai.UnprocessableEntityError`` (status 422 —
    the "policy rejection" semantics used by AI Gateway) and from
    ``AIGuardAbortError`` so existing Datadog-specific handlers keep working.
    """
    global _openai_abort_error_cls
    # Fast path: already cached, no lock needed (assignment to a module
    # attribute is atomic under the GIL).
    if _openai_abort_error_cls is not None:
        return _openai_abort_error_cls

    with _openai_abort_error_cls_lock:
        # Re-check inside the lock: another thread may have built the class
        # while we were blocked on acquisition.
        if _openai_abort_error_cls is not None:
            return _openai_abort_error_cls

        try:
            import openai
        except ImportError:
            return None

        class OpenAIAIGuardAbortError(openai.UnprocessableEntityError, AIGuardAbortError):
            """AI Guard abort error compatible with the OpenAI SDK error hierarchy.

            Catchable as either ``openai.APIError`` / ``openai.UnprocessableEntityError``
            (idiomatic OpenAI error handling, no retry on 422) or
            ``AIGuardAbortError`` (Datadog-specific, exposes ``action`` / ``reason``).

            AIDEV-NOTE: catchability asymmetry vs plain ``AIGuardAbortError``.
            ``AIGuardAbortError`` derives from ``DDBlockException(BaseException)``
            so a generic ``except Exception:`` does NOT catch it (by design — a
            block decision must not be silently swallowed). However,
            ``OpenAIAIGuardAbortError`` *also* inherits from
            ``openai.UnprocessableEntityError``, which is ``Exception``-derived,
            so via MRO this subclass IS catchable by ``except Exception:``.
            That asymmetry is intentional: the OpenAI contrib must keep
            ``except openai.APIError:`` blocks working unchanged for users
            migrating from non-AI-Guard error handling. Code that wants
            uniform block detection across providers should branch on
            ``isinstance(e, AIGuardAbortError)``.
            """

            def __init__(self, action, reason, tags=None, sds=None, tag_probs=None):
                self.action = action
                self.reason = reason
                self.tags = tags
                self.sds = sds or []
                self.tag_probs = tag_probs

                message = f"AIGuardAbortError(action='{action}', reason='{reason}', tags='{tags}')"
                # AIDEV-NOTE: We can't call ``openai.UnprocessableEntityError.__init__``
                # here — its MRO super() chain ends up at ``AIGuardAbortError.__init__``
                # (which requires ``(action, reason)``), not ``Exception.__init__``,
                # so the ``super().__init__(message)`` deep in ``APIError`` raises
                # ``TypeError: missing 'reason'``. Initialize the OpenAI-side
                # attributes directly instead. The block originates from AI Guard,
                # not an OpenAI HTTP call, so ``response`` / ``request`` / ``request_id``
                # are ``None``; only ``status_code`` (422) and ``body`` (the AI Guard
                # decision payload) carry semantic meaning.
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

        _openai_abort_error_cls = OpenAIAIGuardAbortError
        return _openai_abort_error_cls


def _wrap_abort_error(cause):
    """Wrap an ``AIGuardAbortError`` into the OpenAI-compatible variant.

    Returns the original ``cause`` unchanged when the OpenAI SDK is not
    importable — the listener still surfaces a block, just without OpenAI
    exception-hierarchy compatibility (this matches the AI Guard contract:
    catch-by-``AIGuardAbortError`` always works, OpenAI-style ``except
    UnprocessableEntityError`` is a convenience for users who already speak
    the SDK's error vocabulary).
    """
    cls = _get_openai_abort_error_cls()
    if cls is None:
        return cause
    wrapped = cls(
        action=cause.action,
        reason=cause.reason,
        tags=cause.tags,
        sds=cause.sds,
        tag_probs=cause.tag_probs,
    )
    wrapped.__cause__ = cause
    return wrapped


def _get(obj, key, default=None):
    """Read *key* from a dict or object attribute."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
