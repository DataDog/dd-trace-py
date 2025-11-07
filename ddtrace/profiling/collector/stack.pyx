"""CPU profiling collector."""
from __future__ import absolute_import

import logging
import sys
import time
import typing

from ddtrace.trace import Tracer
from ddtrace.internal import core
from ddtrace.internal.datadog.profiling import stack_v2
from ddtrace.internal.settings.profiling import config
from ddtrace.profiling import collector
from ddtrace.profiling.collector import threading


LOG = logging.getLogger(__name__)


def _default_min_interval_time():
    return sys.getswitchinterval() * 2


class StackCollector(collector.PeriodicCollector):
    """Execution stacks collector."""

    __slots__ = (
        "_real_thread",
        "min_interval_time",
        "max_time_usage_pct",
        "nframes",
        "endpoint_collection_enabled",
        "tracer",
        "_last_wall_time",
    )

    def __init__(self,
                 max_time_usage_pct: float = config.max_time_usage_pct,
                 nframes: int = config.max_frames,
                 endpoint_collection_enabled: typing.Optional[bool] = None,
                 tracer: typing.Optional[Tracer] = None):
        super().__init__(interval= _default_min_interval_time())
        if max_time_usage_pct <= 0 or max_time_usage_pct > 100:
            raise ValueError("Max time usage percent must be greater than 0 and smaller or equal to 100")

        # This need to be a real OS thread in order to catch
        self._real_thread: bool = True
        self.min_interval_time: float = _default_min_interval_time()

        self.max_time_usage_pct: float = max_time_usage_pct
        self.nframes: int = nframes
        self.endpoint_collection_enabled: typing.Optional[bool] = endpoint_collection_enabled
        self.tracer: typing.Optional[Tracer] = tracer
        self._last_wall_time: int = 0  # Placeholder for initial value


    def __repr__(self):
        class_name = self.__class__.__name__
        attrs = {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
        attrs_str = ", ".join(f"{k}={v!r}" for k, v in attrs.items())

        slot_attrs = {slot: getattr(self, slot) for slot in self.__slots__ if not slot.startswith("_")}
        slot_attrs_str = ", ".join(f"{k}={v!r}" for k, v in slot_attrs.items())

        return f"{class_name}({attrs_str}, {slot_attrs_str})"


    def _init(self):
        # type: (...) -> None
        self._last_wall_time = time.monotonic_ns()
        if self.tracer is not None:
            link_span = stack_v2.link_span
            core.on("ddtrace.context_provider.activate", link_span)

        # stack v2 requires us to patch the Threading module.  It's possible to do this from the stack v2 code
        # itself, but it's a little bit fiddly and it's easier to make it correct here.
        # TODO take the `threading` import out of here and just handle it in v2 startup
        threading.init_stack_v2()
        stack_v2.set_adaptive_sampling(config.stack.v2_adaptive_sampling)
        stack_v2.start()

    def _start_service(self):
        # type: (...) -> None
        # This is split in its own function to ease testing
        LOG.debug("Profiling StackCollector starting")
        self._init()
        super(StackCollector, self)._start_service()
        LOG.debug("Profiling StackCollector started")

    def _stop_service(self):
        # type: (...) -> None
        LOG.debug("Profiling StackCollector stopping")
        super(StackCollector, self)._stop_service()
        if self.tracer is not None:
            link_span = stack_v2.link_span
            core.reset_listeners("ddtrace.context_provider.activate", link_span)
        LOG.debug("Profiling StackCollector stopped")

        # Also tell the native thread running the v2 sampler to stop, if needed
        stack_v2.stop()

    def _compute_new_interval(self, used_wall_time_ns):
        interval = (used_wall_time_ns / (self.max_time_usage_pct / 100.0)) - used_wall_time_ns
        return max(interval / 1e9, self.min_interval_time)

    def collect(self):
        # Compute wall time
        now = time.monotonic_ns()
        self._last_wall_time = now
        all_events = []

        used_wall_time_ns = time.monotonic_ns() - now
        self.interval = self._compute_new_interval(used_wall_time_ns)

        return all_events
