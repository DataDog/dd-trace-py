"""
Python scheduler plus native source-state delivery for Feature Flagging and Experimentation.
"""

import dataclasses
import threading
import time
import typing as t

from ddtrace.internal.logger import get_logger
from ddtrace.internal.openfeature._native import VariationType
from ddtrace.internal.openfeature._source import FeatureFlagCdnConfig


log = get_logger(__name__)


class HybridSourceError(Exception):
    def __init__(self, message: str, status_code: t.Optional[int] = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


@dataclasses.dataclass(frozen=True)
class HybridPollResult:
    status_code: t.Optional[int] = None
    applied: bool = False
    unchanged: bool = False
    skipped: bool = False
    attempts: int = 0
    etag: t.Optional[str] = None
    error: t.Optional[HybridSourceError] = None


class HybridCdnSource:
    def __init__(
        self,
        config: FeatureFlagCdnConfig,
        source_state_cls: t.Optional[type] = None,
        sleep: t.Callable[[float], None] = time.sleep,
    ):
        self._config = config
        self._sleep = sleep
        hybrid_source_state_cls = source_state_cls or _native_hybrid_source_state_cls()
        self._source_state = hybrid_source_state_cls(
            config.base_url,
            config.api_key,
            config.request_timeout_seconds,
            config.max_retries,
            config.backoff_base_seconds,
        )
        self._poll_lock = threading.Lock()
        self._shutdown = threading.Event()
        self._thread: t.Optional[threading.Thread] = None
        self._last_error: t.Optional[HybridSourceError] = None

    @property
    def is_ready(self) -> bool:
        return bool(getattr(self._source_state, "is_ready", False))

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def last_error(self) -> t.Optional[HybridSourceError]:
        return self._last_error

    def poll_once(self) -> HybridPollResult:
        if not self._poll_lock.acquire(blocking=False):
            error = HybridSourceError("Feature flag hybrid CDN poll already in flight")
            return HybridPollResult(skipped=True, error=error)

        try:
            result = _poll_result_from_native(self._source_state.poll_once())
            self._last_error = result.error
            return result
        finally:
            self._poll_lock.release()

    def start(self) -> None:
        if self.is_running:
            return
        self._shutdown.clear()
        self._thread = threading.Thread(target=self._run, name="ddtrace-openfeature-hybrid-source", daemon=True)
        self._thread.start()

    def shutdown(self, timeout: t.Optional[float] = None) -> bool:
        self._shutdown.set()
        if self._thread is None:
            return True
        self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

    def resolve_flag(
        self,
        flag_key: str,
        expected_type: VariationType,
        evaluation_context: t.Any,
    ):
        return self._source_state.resolve_value(flag_key, expected_type, _context_dict(evaluation_context))

    def _run(self) -> None:
        while not self._shutdown.wait(self._config.poll_interval_seconds):
            result = self.poll_once()
            if result.error is not None:
                log.debug("Feature flag hybrid CDN poll failed: %s", result.error)


def _poll_result_from_native(status: t.Any) -> HybridPollResult:
    error_message = getattr(status, "error", None)
    error = None
    if error_message:
        error = HybridSourceError(
            str(error_message),
            status_code=getattr(status, "status_code", None),
            retryable=bool(getattr(status, "retryable", False)),
        )
    return HybridPollResult(
        status_code=getattr(status, "status_code", None),
        applied=bool(getattr(status, "applied", False)),
        unchanged=bool(getattr(status, "unchanged", False)),
        skipped=bool(getattr(status, "skipped", False)),
        attempts=int(getattr(status, "attempts", 0)),
        etag=getattr(status, "etag", None),
        error=error,
    )


def _context_dict(context: t.Any) -> dict[str, t.Any]:
    context_dict: dict[str, t.Any] = {"targeting_key": None, "attributes": {}}

    if context is None:
        return context_dict
    if isinstance(context, dict):
        targeting_key = context.get("targetingKey")
        if targeting_key is None:
            targeting_key = context.get("targeting_key")
        if targeting_key is not None:
            context_dict["targeting_key"] = targeting_key
        context_dict["attributes"] = context.get("attributes", {})
        return context_dict
    if hasattr(context, "targeting_key"):
        if context.targeting_key is not None:
            context_dict["targeting_key"] = context.targeting_key
        if hasattr(context, "attributes") and context.attributes:
            context_dict["attributes"] = context.attributes
    return context_dict


def _native_hybrid_source_state_cls() -> type:
    from ddtrace.internal.native._native import ffe

    return ffe.HybridSourceState
