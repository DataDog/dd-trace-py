"""Shared helpers for AI Guard provider listeners.

Kept deliberately small: only utilities used by more than one provider
module belong here. Provider-specific schema handling stays in the
respective ``_openai`` / ``_anthropic`` modules.
"""

from collections.abc import Mapping
import importlib
from typing import Any

from ddtrace.aiguard._api_client import AIGuardAbortError
import ddtrace.internal.logger as ddlogger


logger = ddlogger.get_logger(__name__)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read *key* from *obj* whether it's a mapping or an object.

    Uses ``Mapping`` (not just ``dict``) so typed content parts wrapped in
    ``MappingProxyType`` / ``UserDict`` / Pydantic mapping protocol resolve
    correctly -- a strict ``dict`` check would silently drop them.
    """
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def wrap_abort_error(cause: AIGuardAbortError, compound_cls: type[AIGuardAbortError]) -> AIGuardAbortError:
    """Wrap *cause* into the provider-specific compound abort error class.

    Returns *cause* unchanged when *compound_cls* is bare ``AIGuardAbortError``
    (provider SDK not importable): wrapping a class into itself would only
    produce a redundant clone whose ``__cause__`` is the original.
    """
    if compound_cls is AIGuardAbortError:
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


def make_provider_abort_error(name: str, sdk_error_cls: type, module_name: str) -> type[AIGuardAbortError]:
    """Build a provider-specific ``AIGuardAbortError`` subclass at import time.

    The returned class inherits from both *sdk_error_cls* (the provider SDK's
    ``UnprocessableEntityError``) and :class:`AIGuardAbortError`, so a block is
    catchable via ``except <sdk>.APIError`` (Exception-derived) as well as
    ``except AIGuardAbortError``. See the per-provider ``_*_errors`` modules for
    why the SDK ``__init__`` chain cannot be invoked directly.

    *module_name* is stamped onto ``__module__`` to preserve historical span
    ``error.type`` values after the concrete classes were factored out here.
    """

    class _ProviderAIGuardAbortError(sdk_error_cls, AIGuardAbortError):  # type: ignore[misc]
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
            # The SDK ``UnprocessableEntityError.__init__`` MRO chain resolves to
            # ``AIGuardAbortError.__init__`` (not ``Exception.__init__``), so the SDK's
            # ``APIError`` initializer would raise ``TypeError: missing 'reason'``.
            # Initialize the SDK-side attributes directly instead. The block originates
            # from AI Guard, not an HTTP call, so ``response`` / ``request`` /
            # ``request_id`` are ``None``; only ``status_code`` and ``body`` carry meaning.
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

    _ProviderAIGuardAbortError.__name__ = name
    _ProviderAIGuardAbortError.__qualname__ = name
    _ProviderAIGuardAbortError.__module__ = module_name
    return _ProviderAIGuardAbortError


def wrap_provider_abort_error(
    cause: AIGuardAbortError, import_path: str, class_name: str, sdk_name: str
) -> AIGuardAbortError:
    """Wrap *cause* into a provider-specific abort error, importing it lazily.

    *import_path*/*class_name* locate the compound error class (e.g.
    ``ddtrace.aiguard.integrations._openai_errors``/``OpenAIAIGuardAbortError``);
    the module is imported at call time so the optional provider SDK is not
    pulled in eagerly. Falls back to bare :class:`AIGuardAbortError` (still
    catchable by ``except AIGuardAbortError``) when the SDK is not importable.
    """
    exception_class: type[AIGuardAbortError] = AIGuardAbortError
    try:
        # AIDEV-NOTE: import lazily -- ``_*_errors`` pulls in the optional provider
        # SDK at import time. Python's import lock guarantees all concurrent cold
        # imports observe the same class object.
        module = importlib.import_module(import_path)
        exception_class = getattr(module, class_name)
    except ImportError:
        logger.warning(
            "AI Guard: failed to import the %s SDK; falling back to bare AIGuardAbortError. "
            "Install ``%s`` to get SDK-hierarchy compatibility (``except %s.UnprocessableEntityError``).",
            sdk_name,
            sdk_name.lower(),
            sdk_name.lower(),
        )
    return wrap_abort_error(cause, exception_class)
