import ctypes
import os
import time
from typing import Any
from typing import Callable
from typing import Optional
from typing import Union
from typing import cast

import ddtrace
from ddtrace.internal import periodic
from ddtrace.internal import service
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.datadog.profiling._types import ProfilerTracer
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.settings.profiling import config


LOG = get_logger(__name__)

_UNSET: object = object()
# Unset until first lookup; then a libc malloc_trim, or None if missing.
_malloc_trim_fn: Union[Callable[[int], int], None, object] = _UNSET
# Upload counter for _DD_PROFILING_MALLOC_TRIM_EVERY (undocumented experiment knob).
_malloc_trim_upload_count: int = 0
# RSS (bytes) recorded after the last successful trim; used by RSS-growth gate.
_malloc_trim_last_rss_bytes: Optional[int] = None


def _load_malloc_trim() -> Optional[Callable[[int], int]]:
    """Resolve glibc malloc_trim, or None when the symbol is missing."""
    try:
        libc: ctypes.CDLL = ctypes.CDLL(None)
        malloc_trim: Any = libc.malloc_trim
    except (AttributeError, OSError):
        return None
    malloc_trim.argtypes = [ctypes.c_size_t]
    malloc_trim.restype = ctypes.c_int
    return cast(Callable[[int], int], malloc_trim)


def _get_malloc_trim() -> Optional[Callable[[int], int]]:
    global _malloc_trim_fn
    if _malloc_trim_fn is _UNSET:
        _malloc_trim_fn = _load_malloc_trim()
    if _malloc_trim_fn is None:
        return None
    return _malloc_trim_fn  # type: ignore[return-value]


def _read_rss_bytes() -> Optional[int]:
    """Resident set size from /proc/self/statm, or None when unavailable."""
    try:
        with open("/proc/self/statm", encoding="utf-8") as f:
            fields: list[str] = f.read().split()
        if len(fields) < 2:
            return None
        pages: int = int(fields[1])
        page_size: int = os.sysconf("SC_PAGESIZE")
        return pages * page_size
    except (OSError, ValueError):
        return None


def _env_int(name: str, default: int) -> int:
    """Parse an undocumented integer env var; fall back to default on bad input."""
    raw: Optional[str] = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _maybe_malloc_trim() -> None:
    """Return unused glibc heap pages after a profile upload (scheduler thread)."""
    if not config.malloc_trim_enabled:
        return
    malloc_trim: Optional[Callable[[int], int]] = _get_malloc_trim()
    if malloc_trim is None:
        return

    global _malloc_trim_upload_count, _malloc_trim_last_rss_bytes

    every: int = _env_int("_DD_PROFILING_MALLOC_TRIM_EVERY", 1)
    if every < 1:
        every = 1
    _malloc_trim_upload_count += 1
    if _malloc_trim_upload_count % every != 0:
        return

    rss_growth_mb: int = _env_int("_DD_PROFILING_MALLOC_TRIM_RSS_GROWTH_MB", 0)
    rss_before: Optional[int] = _read_rss_bytes()
    if rss_growth_mb > 0 and rss_before is not None and _malloc_trim_last_rss_bytes is not None:
        growth_bytes: int = rss_before - _malloc_trim_last_rss_bytes
        if growth_bytes < rss_growth_mb * 1024 * 1024:
            return

    pad: int = _env_int("_DD_PROFILING_MALLOC_TRIM_PAD", 0)
    if pad < 0:
        pad = 0

    start_ns: int = time.monotonic_ns()
    try:
        ret: int = malloc_trim(pad)
    except Exception:
        LOG.debug("[profiling] malloc_trim failed", exc_info=True)
        return
    duration_ns: int = time.monotonic_ns() - start_ns
    rss_after: Optional[int] = _read_rss_bytes()
    if rss_after is not None:
        _malloc_trim_last_rss_bytes = rss_after
    elif rss_before is not None:
        _malloc_trim_last_rss_bytes = rss_before

    LOG.debug(
        "[profiling] malloc_trim pad=%d ret=%d duration_ns=%d rss_before=%s rss_after=%s",
        pad,
        ret,
        duration_ns,
        rss_before,
        rss_after,
    )


class Scheduler(periodic.PeriodicService):
    """Schedule export of recorded data."""

    def __init__(
        self,
        before_flush: Optional[Callable[[], None]] = None,
        tracer: Optional[ProfilerTracer] = ddtrace.tracer,
        interval: Optional[float] = None,
    ) -> None:
        super().__init__(interval=interval if interval is not None else config.upload_interval)
        self.before_flush: Optional[Callable[[], None]] = before_flush
        self._configured_interval: float = self.interval
        self._last_export: int = 0  # Overridden in _start_service
        self._tracer: Optional[ProfilerTracer] = tracer
        self._enable_code_provenance: bool = config.code_provenance

    def _start_service(self) -> None:
        """Start the scheduler."""
        LOG.debug("[profiling] Starting scheduler")
        ddup.reset(self._tracer)
        super()._start_service()
        self._last_export = time.time_ns()
        LOG.debug("[profiling] Scheduler started")

    def _rollback_start(self) -> None:
        with self._service_lock:
            self._stop_service()
            self.status = service.ServiceStatus.STOPPED

    def flush(self) -> None:
        """Flush profiles to exporters."""
        LOG.debug("[profiling] Flushing profiles")
        if self.before_flush is not None:
            try:
                self.before_flush()
            except Exception:
                LOG.error("[profiling] Scheduler before_flush hook failed", exc_info=True)

        try:
            ddup.upload(self._tracer, self._enable_code_provenance)
        except Exception:
            LOG.error("[profiling] Failed to upload profile", exc_info=True)
        finally:
            self._last_export = time.time_ns()
            # After the upload, not during the request: encoding a profile
            # leaves freed pages in glibc arenas. protobuf used to return
            # those as a side effect; we only do it when the flag is on.
            _maybe_malloc_trim()

    def periodic(self) -> None:
        start_time = time.monotonic()
        try:
            self.flush()
        finally:
            self.interval = max(0, self._configured_interval - (time.monotonic() - start_time))


class ServerlessScheduler(Scheduler):
    """Serverless scheduler that works on, e.g., AWS Lambda.

    The idea with this scheduler is to not sleep 60s, but to sleep 1s and flush out profiles after 60 sleeping period.
    As the service can be frozen a few seconds after flushing out a profile, we want to make sure the next flush is not
    > 60s later, but after at least 60 periods of 1s.

    """

    # We force this interval everywhere
    FORCED_INTERVAL = 1.0
    FLUSH_AFTER_INTERVALS = 60.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if kwargs.get("interval") is None:
            kwargs["interval"] = self.FORCED_INTERVAL

        super().__init__(*args, **kwargs)
        self._profiled_intervals: int = 0

    def periodic(self) -> None:
        self._profiled_intervals += 1

        # Check both the number of intervals and time frame to be sure we don't flush, e.g., empty profiles
        if self._profiled_intervals >= self.FLUSH_AFTER_INTERVALS and (time.time_ns() - self._last_export) >= int(
            self.FORCED_INTERVAL * self.FLUSH_AFTER_INTERVALS * 1e9
        ):
            try:
                super().periodic()
            finally:
                # Override interval so it's always back to the value we need
                self.interval = self.FORCED_INTERVAL
                self._profiled_intervals = 0
