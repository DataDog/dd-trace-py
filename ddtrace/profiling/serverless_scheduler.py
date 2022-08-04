# -*- encoding: utf-8 -*-
import logging

import attr

from ddtrace.internal import compat
from ddtrace.profiling import scheduler


LOG = logging.getLogger(__name__)


@attr.s
class ServerlessScheduler(scheduler.Scheduler):
    _interval = attr.ib(default=1.0, type=float)
    _profiled_intervals = attr.ib(default=0)

    def periodic(self):
        now = compat.time_ns()
        if (now - self._last_export) >= 60 * 1e9 and self._profiled_intervals >= 60:
            self._profiled_intervals = 0
            super(ServerlessScheduler, self).periodic()
            self.interval = 1.0
        else:
            self._profiled_intervals += 1
