"""Automatic DSM pipelines: per-process topic side and in-process link recording.

DSM2-531 "Automatic DSM pipelines from APM services, without DSM enabled".
This module is the pinned recording API (see the issue's PLAN doc):

* ``get_pipelines_reporter()`` returns the process-level reporter, or ``None``
  when ``DD_DATA_STREAMS_PIPELINES_ENABLED`` is false or APM tracing is off.
* ``PipelinesReporter.on_consume(queue_type, cluster, topic, group)`` returns a
  context manager marking a consumed message as the current in-process context
  on this thread.
* ``PipelinesReporter.on_produce(queue_type, cluster, topic)`` records a produce
  side and links it to the thread's current consume scope, or records a source
  (``in_side = -1``) when there is none.

No message ids, no context propagation, no stats: only which topics the service
reads and writes, and which produced message was made in the context of a
consumed one on the same thread. Errors must never raise into application code.
"""
from __future__ import annotations

import os
import threading
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import asbool


log = get_logger(__name__)

# Caps per report window (see PLAN): the report is the sampling.
MAX_SIDES = 256
MAX_LINKS = 1024
MAX_REPORT_BYTES = 60 * 1024
DEFAULT_WINDOW_SECONDS = 300.0

_PRODUCE = 0
_CONSUME = 1

SideKey = Tuple[int, str, str, str, str]  # (direction, queue_type, cluster, topic, group)
LinkKey = Tuple[Optional[SideKey], SideKey]  # (in_side key or None for source, out_side key)


def _pipelines_enabled() -> bool:
    """``DD_DATA_STREAMS_PIPELINES_ENABLED``, default true, independent of ``DD_DATA_STREAMS_ENABLED``.

    The canonical read lives on the tracer config object
    (``_data_streams_pipelines_enabled``); the environment fallback keeps this
    module importable standalone.
    """
    try:
        from ddtrace.internal.settings._config import config

        value = getattr(config, "_data_streams_pipelines_enabled", None)
        if isinstance(value, bool):
            return value
    except Exception:  # noqa: BLE001 - never raise into application code
        log.debug("pipelines: failed to read tracer config", exc_info=True)
    try:
        return asbool(os.environ.get("DD_DATA_STREAMS_PIPELINES_ENABLED", "true"))
    except Exception:  # noqa: BLE001
        return True


class ConsumeScope:
    """Marks a consumed message as the current in-process context on a thread.

    Usable as a context manager. Nesting replaces the previous scope; exiting
    restores it (stack discipline per thread).
    """

    __slots__ = ("_reporter", "_side_key")

    def __init__(self, reporter: "PipelinesReporter", side_key: SideKey) -> None:
        self._reporter = reporter
        self._side_key = side_key

    def __enter__(self) -> "ConsumeScope":
        self._reporter._push_consume_scope(self._side_key)
        return self

    def __exit__(self, *exc: Any) -> None:
        self._reporter._pop_consume_scope(self._side_key)


class PipelinesReporter:
    """Records topic sides and in-process links for the current window."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Interning tables: hot-path lookups are dict hits, no allocation.
        self._sides: Dict[SideKey, int] = {}  # side key -> index in the current window
        self._links: Dict[LinkKey, int] = {}  # link key -> index in the current window
        self._dropped_sides = 0
        self._dropped_links = 0
        self._saw_traffic = False
        # Per-thread stack of current consume scopes (thread id -> list of side keys).
        self._consume_scopes: Dict[int, List[SideKey]] = {}

    # -- public recording API (hot path; never raises) ---------------------

    def on_consume(self, queue_type: str, cluster: str, topic: str, group: str) -> ConsumeScope:
        try:
            side_key = (_CONSUME, queue_type, cluster or "", topic, group or "")
            self._record_side(side_key)
            return ConsumeScope(self, side_key)
        except Exception:  # noqa: BLE001 - T-13: never raise into the application
            log.debug("pipelines: failed to record consume side", exc_info=True)
            return ConsumeScope(self, _NULL_SIDE)

    def on_produce(self, queue_type: str, cluster: str, topic: str) -> None:
        try:
            side_key = (_PRODUCE, queue_type, cluster or "", topic, "")
            self._record_side(side_key)
            in_key = self._current_consume_side()
            self._record_link((in_key, side_key))
        except Exception:  # noqa: BLE001 - T-13
            log.debug("pipelines: failed to record produce side", exc_info=True)

    # -- internals (the full implementation may refine these) ---------------

    def _record_side(self, side_key: SideKey) -> None:
        with self._lock:
            if side_key not in self._sides:
                if len(self._sides) >= MAX_SIDES:
                    self._dropped_sides += 1
                    return
                self._sides[side_key] = len(self._sides)
            self._saw_traffic = True

    def _record_link(self, link_key: LinkKey) -> None:
        with self._lock:
            if link_key not in self._links:
                if len(self._links) >= MAX_LINKS:
                    self._dropped_links += 1
                    return
                self._links[link_key] = len(self._links)

    def _current_consume_side(self) -> Optional[SideKey]:
        stack = self._consume_scopes.get(threading.get_ident())
        return stack[-1] if stack else None

    def _push_consume_scope(self, side_key: SideKey) -> None:
        if side_key is _NULL_SIDE:
            return
        self._consume_scopes.setdefault(threading.get_ident(), []).append(side_key)

    def _pop_consume_scope(self, side_key: SideKey) -> None:
        stack = self._consume_scopes.get(threading.get_ident())
        if stack:
            try:
                stack.remove(side_key)
            except ValueError:  # pragma: no cover - defensive
                pass

    def take_report(self, service: str, env: str) -> Optional[Dict[str, Any]]:
        """Build the ``DdPipelinesReportV1`` dict and reset the window.

        Returns ``None`` when no side was seen in the window (no report).
        Deterministic ordering: sides sorted, then links sorted by their
        indices into the sorted sides.
        """
        with self._lock:
            if not self._saw_traffic:
                return None
            sides = sorted(self._sides)
            index_of = {side: i for i, side in enumerate(sides)}
            links = []
            for (in_key, out_key), _ in sorted(self._links.items(), key=lambda kv: kv[1]):
                in_idx = index_of.get(in_key) if in_key is not None else -1
                out_idx = index_of.get(out_key)
                if in_key is not None and in_idx is None:
                    in_idx = -1  # side dropped in an earlier window: treat as source
                if out_idx is not None:
                    links.append([in_idx, out_idx])
            self._sides = {}
            self._links = {}
            self._saw_traffic = False
            return {
                "service": service,
                "env": env or "",
                "sides": [list(side) for side in sides],
                "links": links,
            }


_NULL_SIDE: SideKey = (_CONSUME, "", "", "", "")

_reporter: Optional[PipelinesReporter] = None
_reporter_lock = threading.Lock()


def get_pipelines_reporter() -> Optional[PipelinesReporter]:
    """Return the process-level pipelines reporter, or ``None`` when disabled."""
    global _reporter
    if not _pipelines_enabled():
        return None
    if _reporter is None:
        with _reporter_lock:
            if _reporter is None:
                _reporter = PipelinesReporter()
    return _reporter
