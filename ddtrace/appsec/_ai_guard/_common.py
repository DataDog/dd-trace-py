"""Shared helpers for AI Guard provider listeners.

Kept deliberately small: only utilities used by more than one provider
module belong here. Provider-specific schema handling stays in the
respective ``_openai`` / ``_anthropic`` modules.
"""

import threading

from ddtrace.appsec.ai_guard._api_client import AIGuardAbortError
from ddtrace.appsec.ai_guard._api_client import Options
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.settings.asm import ai_guard_config


logger = ddlogger.get_logger(__name__)


def _get(obj, key, default=None):
    """Read *key* from a dict or object attribute.

    Provider SDKs deliver request-side payloads as user-supplied dicts and
    response-side payloads as SDK model objects with attributes. This helper
    abstracts over both shapes so converters can stay agnostic.
    """
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


# AIDEV-NOTE: Compound abort-error classes are built lazily on first block.
# Both ``_openai.py`` and ``_anthropic.py`` are imported unconditionally by
# the AI Guard listener, but the provider SDKs are optional runtime
# dependencies — eagerly importing them would break AI Guard initialisation
# in environments that only use a different provider.
#
# The cache uses double-checked locking: the unsynchronised check-then-act
# pattern would let two threads simultaneously enter the build branch and
# each create their own class, so callers that captured ``type(err)`` from
# one block would silently stop matching errors from a concurrent block.
_compound_abort_error_cache: dict = {}
_compound_abort_error_lock = threading.Lock()


def build_compound_abort_error_cls(provider_name, sdk_error_cls):
    """Return a cached compound abort-error class for *provider_name*.

    The class subclasses both *sdk_error_cls* (the provider SDK's 422
    ``UnprocessableEntityError`` — "policy rejection" semantics used by AI
    Gateway) and ``AIGuardAbortError`` so existing Datadog-specific handlers
    keep working.

    AIDEV-NOTE: catchability asymmetry vs plain ``AIGuardAbortError``.
    ``AIGuardAbortError`` derives from ``DDBlockException(BaseException)``
    so a generic ``except Exception:`` does NOT catch it (by design — a
    block decision must not be silently swallowed). However, the compound
    subclass *also* inherits from the SDK's ``UnprocessableEntityError``,
    which is ``Exception``-derived, so via MRO this subclass IS catchable
    by ``except Exception:``. That asymmetry is intentional: the provider
    contrib must keep ``except <sdk>.APIError:`` blocks working unchanged
    for users migrating from non-AI-Guard error handling. Code that wants
    uniform block detection across providers should branch on
    ``isinstance(e, AIGuardAbortError)``.
    """
    cached = _compound_abort_error_cache.get(provider_name)
    if cached is not None:
        return cached

    with _compound_abort_error_lock:
        cached = _compound_abort_error_cache.get(provider_name)
        if cached is not None:
            return cached

        class CompoundAIGuardAbortError(sdk_error_cls, AIGuardAbortError):
            def __init__(self, action, reason, tags=None, sds=None, tag_probs=None):
                self.action = action
                self.reason = reason
                self.tags = tags
                self.sds = sds or []
                self.tag_probs = tag_probs

                message = f"AIGuardAbortError(action='{action}', reason='{reason}', tags='{tags}')"
                # AIDEV-NOTE: We can't call the SDK ``UnprocessableEntityError.__init__``
                # here — its MRO super() chain ends up at ``AIGuardAbortError.__init__``
                # (which requires ``(action, reason)``), not ``Exception.__init__``,
                # so the ``super().__init__(message)`` deep in the SDK's ``APIError``
                # raises ``TypeError: missing 'reason'``. Initialize the SDK-side
                # attributes directly instead. The block originates from AI Guard,
                # not an HTTP call, so ``response`` / ``request`` / ``request_id``
                # are ``None``; only ``status_code`` (422) and ``body`` (the AI
                # Guard decision payload) carry semantic meaning.
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

        CompoundAIGuardAbortError.__name__ = f"{provider_name}AIGuardAbortError"
        CompoundAIGuardAbortError.__qualname__ = CompoundAIGuardAbortError.__name__
        _compound_abort_error_cache[provider_name] = CompoundAIGuardAbortError
        return CompoundAIGuardAbortError


def wrap_abort_error(cause, compound_cls):
    """Wrap *cause* into the provider-specific compound abort error class.

    Returns *cause* unchanged when *compound_cls* is ``None`` (provider SDK
    not importable) — the listener still surfaces a block, just without
    SDK-hierarchy compatibility.
    """
    if compound_cls is None:
        return cause
    wrapped = compound_cls(
        action=cause.action,
        reason=cause.reason,
        tags=cause.tags,
        sds=cause.sds,
        tag_probs=cause.tag_probs,
    )
    wrapped.__cause__ = cause
    return wrapped


def evaluate_messages(client, messages, compound_cls, error_log_label):
    """Run AI Guard evaluation on *messages*, raising the provider-specific
    compound abort error on block decisions.

    Non-abort exceptions are logged at debug level and swallowed — AI Guard
    fails open so backend transport errors never break the user's SDK call.
    """
    try:
        client.evaluate(messages, Options(block=ai_guard_config._ai_guard_block))
    except AIGuardAbortError as e:
        raise wrap_abort_error(e, compound_cls)
    except Exception:
        logger.debug("Failed to evaluate %s", error_log_label, exc_info=True)
