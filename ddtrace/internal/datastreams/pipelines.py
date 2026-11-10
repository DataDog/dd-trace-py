"""Automatic DSM pipelines: per-process topic side and in-process link recording.

* ``get_pipelines_reporter()``: the process reporter, or ``None`` when
  ``DD_DATA_STREAMS_PIPELINES_ENABLED`` is false or APM tracing is off.
* ``set_current_consume(queue_type, cluster, topic, group)``: records a consume
  side and makes it the thread's current message until the next consume.
* ``on_produce(queue_type, cluster, topic)``: records a produce side linked to
  the current consume side, or a source link (``in_side = -1``) without one.
* ``take_report(service, env, due=False)``: builds and resets the
  ``DdPipelinesReportV1`` window; with ``due=True`` only once the window elapsed.

The cluster is the Kafka cluster id, or ``""`` when unknown; never bootstrap
servers. No message ids, propagation or stats. Errors never raise into the app.

The current message lives in ``current_message``, shared with classic DSM's
pathway context, so both features follow one in-process rule.

With DSM on, the report rides the DSM payload; otherwise a lazily started
thread POSTs to /v0.1/pipeline_stats. No thread or atexit hook until the first side.
"""

from __future__ import annotations

import gzip
import os
import threading
import time
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace.internal import atexit as ddtrace_atexit
from ddtrace.internal import compat
from ddtrace.internal import process_tags
from ddtrace.internal.constants import DEFAULT_SERVICE_NAME
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.internal.settings._config import config
from ddtrace.version import __version__

from .._encoding import packb
from ..hostname import get_hostname
from . import current_message
from . import transport


log = get_logger(__name__)

# Caps per report window: the report is the sampling.
MAX_SIDES = 256
MAX_LINKS = 1024
MAX_REPORT_BYTES = 60 * 1024
DEFAULT_WINDOW_SECONDS = 300.0
# Top-level payload key carrying the report bytes (golden value).
REPORT_PAYLOAD_KEY = "DdPipelinesReportV1"
PIPELINES_STATS_ENDPOINT = "/v0.1/pipeline_stats"

_PRODUCE = "produce"
_CONSUME = "consume"

SideKey = tuple[str, str, str, str, str]  # (direction, queue_type, cluster, topic, group)
LinkKey = tuple[Optional[SideKey], SideKey]  # (in_side key or None for source, out_side key)


# -- module drop counters (window caps and report size cap) ------------------
# Surface drops in logs; also readable by tests via drop_counts().

_drop_counters_lock = threading.Lock()
_dropped_sides = 0
_dropped_links = 0


def drop_counts() -> tuple[int, int]:
    """Return the (sides, links) dropped so far in this process.

    Sides and links are dropped when the per-window caps are exceeded at record
    time, or when the 60 KiB report size cap trims the window at flush time.
    """
    with _drop_counters_lock:
        return _dropped_sides, _dropped_links


def _count_drops(sides: int = 0, links: int = 0) -> None:
    global _dropped_sides, _dropped_links
    with _drop_counters_lock:
        _dropped_sides += sides
        _dropped_links += links


def _report_fits(report: dict[str, Any]) -> bool:
    return len(packb(report)) <= MAX_REPORT_BYTES


def _trim_report_to_size_cap(report: dict[str, Any]) -> tuple[int, int]:
    """Trim report in place to MAX_REPORT_BYTES; return (dropped_links, dropped_sides).

    Rarely hit (needs 256/1024 entries with long names): drop links from the
    tail first, then sides, and with a dropped side every link that referenced
    it. Deterministic — the lists are sorted, so the tail is the highest
    indices. The last side always stays, so the report is never empty.
    """
    if _report_fits(report):
        return 0, 0

    dropped_links = 0
    dropped_sides = 0
    while not _report_fits(report):
        if report["links"]:
            report["links"].pop()
            dropped_links += 1
            continue
        if len(report["sides"]) <= 1:
            break  # a single pathological side stays; the agent caps it at intake
        report["sides"].pop()
        dropped_sides += 1
        n = len(report["sides"])
        kept = [lnk for lnk in report["links"] if lnk[0] < n and lnk[1] < n]
        dropped_links += len(report["links"]) - len(kept)
        report["links"] = kept
    return dropped_links, dropped_sides


class PipelinesReporter:
    """Records topic sides and in-process links for the current window.

    The hot path (set_current_consume/on_produce) is one lock
    acquisition with interned dict lookups per message: repeated sides and links
    intern to existing entries, so per-window memory is bounded by
    MAX_SIDES/MAX_LINKS.
    """

    def __init__(self, interval: Optional[float] = None, clock: Callable[[], float] = time.monotonic) -> None:
        self._lock = threading.Lock()
        # Interning tables: hot-path lookups are dict hits, no accumulation.
        self._sides: dict[SideKey, int] = {}  # side key -> index in the current window
        self._links: dict[LinkKey, bool] = {}  # link key -> recorded (dict as ordered set)
        self._saw_traffic = False
        self._cap_logged = False  # warn once per window when a cap is first hit
        self._clock = clock
        self._interval = self._resolve_interval(interval)
        self._window_start = clock()

    @staticmethod
    def _resolve_interval(interval: Optional[float]) -> float:
        if interval is None:
            try:
                interval = float(config._data_streams_pipelines_interval)
            except Exception:  # noqa: BLE001 - never raise into application code
                interval = DEFAULT_WINDOW_SECONDS
        if not interval or interval <= 0:
            return DEFAULT_WINDOW_SECONDS
        return interval

    # -- public recording API (hot path; never raises) ---------------------

    def set_current_consume(self, queue_type: str, cluster: str, topic: str, group: str) -> None:
        """Record a consume side and make it the thread's current context until replaced.

        The only consume entry point, and the in-process rule classic DSM uses:
        the current message is replaced by the next consume on the thread and
        never cleared otherwise. Consumer hooks call this at consume time —
        message processing happens after the instrumented call returns.
        """
        try:
            side_key = (_CONSUME, queue_type or "", cluster or "", topic or "", group or "")
            with self._lock:
                recorded = self._intern_side(side_key) is not None
            self._set_current_consume_key(side_key)
            if recorded:
                self._on_traffic()
        except Exception:  # noqa: BLE001 - T-13
            log.debug("pipelines: failed to record consume side", exc_info=True)

    def on_produce(self, queue_type: str, cluster: str, topic: str) -> None:
        try:
            side_key = (_PRODUCE, queue_type or "", cluster or "", topic or "", "")
            in_key = self._current_consume_key()
            with self._lock:
                out_idx = self._intern_side(side_key)
                if out_idx is not None:
                    if in_key is None:
                        # no consume context on this thread: source link (in_side = -1)
                        self._intern_link(None, side_key)
                    else:
                        # refresh the consumed side into this window so the link
                        # stays representable even across a window boundary
                        in_idx = self._intern_side(in_key)
                        self._intern_link(None if in_idx is None else in_key, side_key)
            if out_idx is not None:
                self._on_traffic()
        except Exception:  # noqa: BLE001 - T-13
            log.debug("pipelines: failed to record produce side", exc_info=True)

    # -- report taking ------------------------------------------------------

    def take_report(self, service: str, env: str, due: bool = False) -> Optional[dict[str, Any]]:
        """Build the ``DdPipelinesReportV1`` dict and reset the window.

        Returns ``None`` when no side was seen in the window (or, with
        ``due=True``, when the flush window has not elapsed yet — one report
        per window, and the window rolls whether or not traffic was seen).
        Deterministic: sides sorted, then links sorted by their indices into
        the sorted sides. The 60 KiB report cap is enforced here by dropping
        links first, then sides, from the tail (drops counted).
        """
        try:
            with self._lock:
                if due:
                    now = self._clock()
                    if now - self._window_start < self._interval:
                        return None
                    self._window_start = now
                return self._take_report_locked(service, env)
        except Exception:  # noqa: BLE001 - never raise into the application
            log.debug("pipelines: failed to build the window report", exc_info=True)
            self._reset_window()
            return None

    # -- internals ----------------------------------------------------------

    def _intern_side(self, side_key: SideKey) -> Optional[int]:
        """Intern side_key into the current window; return its index or None when the cap dropped it."""
        idx = self._sides.get(side_key)
        if idx is None:
            if len(self._sides) >= MAX_SIDES:
                self._warn_cap_once()
                _count_drops(sides=1)
                return None
            idx = len(self._sides)
            self._sides[side_key] = idx
        self._saw_traffic = True
        return idx

    def _intern_link(self, in_key: Optional[SideKey], out_key: SideKey) -> None:
        """Intern the (in_side, out_side) link; both sides must be interned first."""
        link_key = (in_key, out_key)
        if link_key in self._links:
            return
        if len(self._links) >= MAX_LINKS:
            self._warn_cap_once()
            _count_drops(links=1)
            return
        self._links[link_key] = True

    def _warn_cap_once(self) -> None:
        if not self._cap_logged:
            self._cap_logged = True
            log.warning(
                "pipelines: reached the %d side / %d link window caps; further distinct sides or links are dropped "
                "this window",
                MAX_SIDES,
                MAX_LINKS,
            )

    def _current_consume_key(self) -> Optional[SideKey]:
        # The current consumed-message context is the shared slot classic DSM
        # also uses (ddtrace.internal.datastreams.current_message): one
        # mechanism, two readers (DSM pathway, pipelines side).
        return current_message.current_pipelines_side()

    def _set_current_consume_key(self, side_key: SideKey) -> None:
        current_message.set_pipelines_side(side_key)

    def _take_report_locked(self, service: str, env: str) -> Optional[dict[str, Any]]:
        self._window_start = self._clock()
        if not self._saw_traffic:
            return None
        sides = sorted(self._sides)
        index_of = {side: i for i, side in enumerate(sides)}
        links = []
        for in_key, out_key in self._links:
            links.append([-1 if in_key is None else index_of[in_key], index_of[out_key]])
        links.sort()
        report = {
            "service": service,
            "env": env or "",
            "sides": [list(side) for side in sides],
            "links": links,
        }
        dropped_links, dropped_sides = _trim_report_to_size_cap(report)
        if dropped_links or dropped_sides:
            _count_drops(sides=dropped_sides, links=dropped_links)
            log.warning(
                "pipelines: window report exceeded %d bytes; dropped %d links and %d sides",
                MAX_REPORT_BYTES,
                dropped_links,
                dropped_sides,
            )
        self._sides = {}
        self._links = {}
        self._saw_traffic = False
        self._cap_logged = False
        return report

    def _reset_window(self) -> None:
        try:
            with self._lock:
                self._sides = {}
                self._links = {}
                self._saw_traffic = False
                self._cap_logged = False
                self._window_start = self._clock()
        except Exception:  # noqa: BLE001
            log.debug("pipelines: failed to reset the window", exc_info=True)

    def _on_traffic(self) -> None:
        """Start the flush lifecycle on the first recorded side (process singleton only)."""
        if self is not _reporter:
            # Directly-constructed reporters are managed manually by tests/tools.
            return
        if _lifecycle_started:
            return
        _ensure_lifecycle(self)

    def _reset_after_fork(self) -> None:
        """Fresh window in a forked child (T-12): no data, no locks inherited held.

        Only the forking thread survives in the child and this runs on it, so
        resetting without taking self._lock (which may have been held by another
        thread at fork time) is safe; the lock is replaced outright.
        """
        self._lock = threading.Lock()
        # Fresh window and fresh pipelines context in the child; the DSM part of
        # the shared current-message slot is untouched (classic DSM fork
        # behavior is unchanged).
        current_message.reset_pipelines_side()
        self._sides = {}
        self._links = {}
        self._saw_traffic = False
        self._cap_logged = False
        self._window_start = self._clock()


class _StandaloneFlusher:
    """Flushes pipeline reports to the agent when DSM is disabled.

    Mirrors processor.DataStreamsProcessor's connection handling, headers and
    error handling; the envelope is its own /v0.1/pipeline_stats payload with
    ``Stats: []`` and ``DdPipelinesReportV1`` as the last key.
    """

    def __init__(self, reporter: PipelinesReporter, agent_url: Optional[str] = None, timeout: float = 1.0) -> None:
        self._reporter = reporter
        self._agent_url = agent_url or agent_config.trace_agent_url
        self._agent_endpoint = "%s%s" % (self._agent_url, PIPELINES_STATS_ENDPOINT)
        self._timeout = timeout
        self._hostname = compat.ensure_text(get_hostname())
        self._headers: dict[str, str] = {
            "Datadog-Meta-Lang": "python",
            "Datadog-Meta-Tracer-Version": __version__,
            "Content-Type": "application/msgpack",
            "Content-Encoding": "gzip",
        }
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="ddtrace.internal.datastreams.pipelines.flusher",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: Optional[float] = None) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout)

    def _run(self) -> None:
        # Wait out the window; the reporter decides whether a report is due.
        while not self._stop_event.wait(self._reporter._interval):
            try:
                self.flush_once()
            except Exception:  # noqa: BLE001 - the flush thread must never die
                log.debug("pipelines: background flush failed", exc_info=True)

    def flush_once(self, force: bool = False) -> None:
        """Flush the current window as its own /v0.1/pipeline_stats payload.

        With force=True the window is flushed even if it has not fully elapsed
        (used by the atexit hook, mirroring the DSM shutdown flush).
        """
        service = compat.ensure_text(config._get_service(DEFAULT_SERVICE_NAME))  # type: ignore[no-untyped-call]
        env = compat.ensure_text(config.env or "")
        report = self._reporter.take_report(service, env, due=not force)
        if report is None:
            return
        raw_payload: dict[str, Any] = {
            "Service": service,
            "TracerVersion": __version__,
            "Lang": "python",
            "Hostname": self._hostname,
            "Stats": [],
        }
        if config.env:
            raw_payload["Env"] = compat.ensure_text(config.env)
        if config.version:
            raw_payload["Version"] = compat.ensure_text(config.version)
        if p_tags := process_tags.process_tags_list:
            raw_payload["ProcessTags"] = p_tags
        # The report key must be the LAST key of the payload (golden value).
        raw_payload[REPORT_PAYLOAD_KEY] = packb(report)
        self._post(gzip.compress(packb(raw_payload), 1))

    def _post(self, payload: bytes) -> None:
        """POST the payload via the shared transport; connection failures are
        logged and swallowed (the flush thread must never die; the next window
        retries).
        """
        try:
            transport.post(
                self._agent_url,
                PIPELINES_STATS_ENDPOINT,
                payload,
                self._headers,
                self._timeout,
                what="pipelines report",
                unsupported_feature="pipeline stats",
                ok_noun="pipelines report",
            )
        except transport.TransportError:
            log.debug(
                "failed to submit pipelines report to the Datadog agent at %s", self._agent_endpoint, exc_info=True
            )


# -- process-level lifecycle --------------------------------------------------

_reporter: Optional[PipelinesReporter] = None
_reporter_lock = threading.Lock()

_flusher: Optional[_StandaloneFlusher] = None
_lifecycle_started = False
_atexit_registered = False
_lifecycle_lock = threading.Lock()


def _pipelines_enabled() -> bool:
    """``DD_DATA_STREAMS_PIPELINES_ENABLED`` (default true), independent of ``DD_DATA_STREAMS_ENABLED``."""
    try:
        return bool(config._data_streams_pipelines_enabled)
    except Exception:  # noqa: BLE001 - never raise into application code
        log.debug("pipelines: failed to read the pipelines setting", exc_info=True)
        return True


def _apm_tracing_off() -> bool:
    """Whether APM tracing is disabled for this process (contract: reporter is None then)."""
    try:
        # Deliberately lazy, like ddtrace._trace.product: the root package imports
        # this module's package tree at startup, so it cannot be a module import.
        from ddtrace import tracer

        return not tracer.enabled
    except Exception:  # noqa: BLE001
        return False


def _ensure_lifecycle(reporter: PipelinesReporter) -> None:
    """Create the standalone flush thread and atexit hook on the first recorded side.

    With DSM enabled the report is carried by the DSM processor's periodic
    payload instead, so no thread or hook is started here.
    """
    global _flusher, _lifecycle_started, _atexit_registered
    with _lifecycle_lock:
        if _lifecycle_started:
            return
        _lifecycle_started = True
        try:
            if config._data_streams_enabled:
                return
        except Exception:  # noqa: BLE001
            log.debug("pipelines: failed to read the DSM setting", exc_info=True)
        _flusher = _StandaloneFlusher(reporter)
        _flusher.start()
        if not _atexit_registered:
            _atexit_registered = True
            ddtrace_atexit.register(_atexit_flush)


def _atexit_flush() -> None:
    """Best-effort flush of the current window on normal interpreter exit."""
    try:
        flusher = _flusher
        if flusher is not None:
            flusher.flush_once(force=True)
    except Exception:  # noqa: BLE001 - never raise at exit
        log.debug("pipelines: atexit flush failed", exc_info=True)


def _after_fork_in_child() -> None:
    """Give the forked child a fresh window: no inherited flush thread, no duplicated report (T-12)."""
    global _flusher, _lifecycle_started, _lifecycle_lock, _reporter_lock
    try:
        # Only the forking thread survives in the child; locks whose state may
        # have been inherited held are replaced outright (never taken here).
        _flusher = None
        _lifecycle_started = False
        _lifecycle_lock = threading.Lock()
        _reporter_lock = threading.Lock()
        if _reporter is not None:
            _reporter._reset_after_fork()
    except Exception:  # noqa: BLE001 - never raise into the app from a fork hook
        log.debug("pipelines: failed to reset the window after fork", exc_info=True)


try:
    os.register_at_fork(after_in_child=_after_fork_in_child)
except Exception:  # pragma: no cover - platforms without register_at_fork
    log.debug("pipelines: os.register_at_fork unavailable", exc_info=True)


def get_pipelines_reporter() -> Optional[PipelinesReporter]:
    """Return the process-level pipelines reporter, or ``None`` when disabled.

    ``None`` when ``DD_DATA_STREAMS_PIPELINES_ENABLED`` is false or APM tracing
    is off. The flush thread and atexit hook are started lazily on the first
    recorded side, not here.
    """
    global _reporter
    if not _pipelines_enabled() or _apm_tracing_off():
        return None
    if _reporter is None:
        with _reporter_lock:
            if _reporter is None:
                _reporter = PipelinesReporter()
    return _reporter
