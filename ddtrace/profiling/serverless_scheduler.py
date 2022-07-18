# -*- encoding: utf-8 -*-
import logging

import attr

from ddtrace.internal import compat
from ddtrace.internal.utils import attr as attr_utils
from ddtrace.profiling import scheduler


LOG = logging.getLogger(__name__)


@attr.s
class ServerlessScheduler(scheduler.Scheduler):
    _interval = attr.ib(1)
    _total_profiled_seconds = attr.ib(default=0)

    def periodic(self):
        now = compat.time_ns()
        # Guard against _last_export not being set
        last_export = self._last_export or compat.time_ns()
        if now - last_export >= 60 * 1e9 and self._total_profiled_seconds >= 60:
            self._total_profiled_seconds = 0
            start_time = compat.monotonic()
            try:
                self.flush()
            finally:
                self.interval = max(0, self._configured_interval - (compat.monotonic() - start_time))
        else:
            self._total_profiled_seconds += self._interval
