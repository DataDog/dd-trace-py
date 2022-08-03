# -*- encoding: utf-8 -*-
import logging

import attr

from ddtrace.internal import compat
from ddtrace.internal.utils import attr as attr_utils
from ddtrace.profiling import scheduler


LOG = logging.getLogger(__name__)


@attr.s
class ServerlessScheduler(scheduler.Scheduler):
    _interval = attr.ib(default=1.0, type=float)
    _profiled_intervals = attr.ib(default=0)
    _init_time = compat.time_ns()

    def periodic(self):
        now = compat.time_ns()
        # Guard against _last_export not being set
        last_export = self._last_export or self._init_time
        if (now - last_export >= int(self._interval) * 1e9) and (self._profiled_intervals >= 60):
            self._profiled_intervals = 0
            super(ServerlessScheduler, self).periodic()
        else:
            self._profiled_intervals += 1
